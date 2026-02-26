"""
Universal API Key Manager — round-robin cycling with auto-retry for any service.

Supports Gemini, Pexels, Google CSE — each with independent key pools and cycling toggles.
Keys and cycling settings are loaded from settings_store.
"""

import asyncio
import logging
import threading
from typing import Optional

from google import genai
from google.genai import types as genai_types

logger = logging.getLogger(__name__)


class KeyCycler:
    """
    Round-robin key cycler for a single service.
    Thread-safe index rotation with optional cycling mode.
    """

    def __init__(self, service_name: str, keys: list[str], cycling: bool = True):
        self.service_name = service_name
        self._keys = keys
        self._cycling = cycling
        self._index = 0
        self._lock = threading.Lock()

    @property
    def key_count(self) -> int:
        return len(self._keys)

    @property
    def cycling(self) -> bool:
        return self._cycling

    def reload(self, keys: list[str], cycling: bool):
        """Hot-reload keys and cycling setting."""
        with self._lock:
            self._keys = keys
            self._cycling = cycling
            if self._index >= len(keys):
                self._index = 0

    def get_key(self) -> Optional[str]:
        """Get the next key. Cycles if cycling is on, otherwise returns the first key."""
        if not self._keys:
            return None
        with self._lock:
            if self._cycling:
                key = self._keys[self._index % len(self._keys)]
                self._index += 1
            else:
                key = self._keys[0]
            return key

    def get_all_keys(self) -> list[str]:
        """Get all keys for retry purposes."""
        return list(self._keys)


class APIKeyManager:
    """
    Manages key pools for multiple services.
    Provides Gemini client helpers and generic key access.
    """

    def __init__(self):
        self._cyclers: dict[str, KeyCycler] = {}
        self._load_from_store()

    def _load_from_store(self):
        """Load key configurations from settings store."""
        try:
            from app.settings_store import get_settings
            settings = get_settings()
            api_keys = settings.get("api_keys", {})

            for service in ["gemini", "pexels", "google_cse", "reddit"]:
                svc_data = api_keys.get(service, {"keys": [], "cycling": False})
                keys = svc_data.get("keys", [])
                cycling = svc_data.get("cycling", False)
                self._cyclers[service] = KeyCycler(service, keys, cycling)

            logger.info(
                f"API Key Manager loaded: "
                f"Gemini={self._cyclers['gemini'].key_count} keys, "
                f"Pexels={self._cyclers['pexels'].key_count} keys, "
                f"CSE={self._cyclers['google_cse'].key_count} keys"
            )
        except Exception as e:
            logger.error(f"Failed to load API keys: {e}")

    def reload(self):
        """Reload keys from settings store (call after key changes)."""
        self._load_from_store()

    def get_cycler(self, service: str) -> KeyCycler:
        """Get the key cycler for a service."""
        if service not in self._cyclers:
            self._cyclers[service] = KeyCycler(service, [], False)
        return self._cyclers[service]

    def get_key(self, service: str) -> Optional[str]:
        """Get the current key for a service."""
        return self.get_cycler(service).get_key()

    # ── Gemini helpers ─────────────────────────────────────────────────

    async def gemini_generate(
        self,
        model: str,
        contents,
        config: Optional[genai_types.GenerateContentConfig] = None,
        max_retries: int = 0,
    ):
        """
        Generate content via Gemini with key cycling and auto-retry on rate limits.
        """
        cycler = self.get_cycler("gemini")
        all_keys = cycler.get_all_keys()

        if not all_keys:
            raise RuntimeError("No Gemini API keys configured. Add keys in the admin panel.")

        if max_retries == 0:
            max_retries = len(all_keys)

        last_error = None

        for attempt in range(max_retries):
            key = cycler.get_key()
            key_preview = f"{key[:8]}..." if key and len(key) > 8 else "***"

            try:
                client = genai.Client(api_key=key)
                kwargs = {"model": model, "contents": contents}
                if config:
                    kwargs["config"] = config
                response = await client.aio.models.generate_content(**kwargs)
                return response

            except Exception as e:
                error_str = str(e).lower()
                is_rate_limit = any(
                    phrase in error_str
                    for phrase in ["429", "rate limit", "resource exhausted", "quota", "too many requests"]
                )

                if is_rate_limit and attempt < max_retries - 1:
                    logger.warning(f"Gemini key {key_preview} hit rate limit (attempt {attempt + 1}/{max_retries}). Rotating...")
                    last_error = e
                    await asyncio.sleep(0.5)
                    continue
                else:
                    raise e

        raise last_error or RuntimeError("All Gemini API keys exhausted")

    # ── Pexels helper ──────────────────────────────────────────────────

    def get_pexels_key(self) -> Optional[str]:
        """Get a Pexels API key (cycled if cycling is on)."""
        return self.get_key("pexels")

    # ── Reddit helper ──────────────────────────────────────────────────

    def get_reddit_key(self) -> Optional[str]:
        """Get a Reddit API key (client_id:client_secret cycled if cycling is on)."""
        return self.get_key("reddit")

    # ── Google CSE helper ──────────────────────────────────────────────

    def get_cse_key(self) -> Optional[str]:
        """Get a Google CSE API key (cycled if cycling is on)."""
        return self.get_key("google_cse")

    def get_cse_cx(self) -> str:
        """Get the Google CSE CX ID."""
        try:
            from app.settings_store import get_settings
            return get_settings().get("api_keys", {}).get("google_cse_cx", "")
        except Exception:
            return ""

    def has_image_search(self) -> bool:
        """Check if CSE image search is available."""
        return bool(self.get_cse_key() and self.get_cse_cx())


# ── Singleton ──────────────────────────────────────────────────────────
_manager: Optional[APIKeyManager] = None


def get_key_manager() -> APIKeyManager:
    """Get the global API key manager instance."""
    global _manager
    if _manager is None:
        _manager = APIKeyManager()
    return _manager


def reload_key_manager():
    """Reload the global key manager from settings store."""
    global _manager
    if _manager:
        _manager.reload()
    else:
        _manager = APIKeyManager()

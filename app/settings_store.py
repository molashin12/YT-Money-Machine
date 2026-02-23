"""
Settings Store — persistent JSON-based storage for all dynamic configuration.

Stores channels, API keys (with cycling toggles), and preferences in `data/settings.json`.
Provides CRUD operations for channels and API keys.
"""

import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
ASSETS_DIR = BASE_DIR / "assets"
CHANNELS_DIR = ASSETS_DIR / "channels"
MUSIC_DIR = ASSETS_DIR / "music"

# Ensure dirs exist
for d in [DATA_DIR, CHANNELS_DIR, MUSIC_DIR]:
    d.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS = {
    "api_keys": {
        "gemini": {"keys": [], "cycling": True},
        "pexels": {"keys": [], "cycling": False},
        "google_cse": {"keys": [], "cycling": False},
        "google_cse_cx": "",
        "telegram_bot_token": "",
        "youtube_oauth": {"client_id": "", "client_secret": ""},
    },
    "channels": [],
    "cron_jobs": [],
    "team_members": [],
}

DEFAULT_CHANNEL = {
    "name": "",
    "slug": "",
    "description": "",  # Channel content style guide for AI
    "primary_color": "#1a1a2e",
    "accent_color": "#e94560",
    "text_color": "#ffffff",
    "card_mode": "pillow",  # "pillow" or "ai"
    "sound_mode": "random",  # "random", "none", "specific"
    "sound_file": None,  # filename in assets/music/ (when sound_mode="specific")
    "video_duration": 5,
    "youtube_tokens": {},  # OAuth2 tokens for YouTube upload
}


# ── Core I/O ────────────────────────────────────────────────────────────


def _read_settings() -> dict:
    """Read settings from disk, migrating from legacy files if needed."""
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Ensure all top-level keys exist
            for key, val in DEFAULT_SETTINGS.items():
                if key not in data:
                    data[key] = val
            return data
        except Exception as e:
            logger.error(f"Failed to read settings: {e}")

    # First run: migrate from legacy .env and channels.json
    return _migrate_legacy()


def _write_settings(data: dict) -> None:
    """Write settings to disk."""
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _migrate_legacy() -> dict:
    """Migrate from legacy .env / channels.json to settings.json."""
    logger.info("Migrating legacy configuration to settings.json...")
    data = json.loads(json.dumps(DEFAULT_SETTINGS))

    # Migrate .env API keys
    gemini_keys_raw = os.getenv("GEMINI_API_KEY", "")
    if gemini_keys_raw:
        data["api_keys"]["gemini"]["keys"] = [
            k.strip() for k in gemini_keys_raw.split(",") if k.strip()
        ]
    pexels_key = os.getenv("PEXELS_API_KEY", "")
    if pexels_key:
        data["api_keys"]["pexels"]["keys"] = [pexels_key]
    cse_key = os.getenv("GOOGLE_CSE_API_KEY", "")
    if cse_key:
        data["api_keys"]["google_cse"]["keys"] = [cse_key]
    cse_cx = os.getenv("GOOGLE_CSE_CX", "")
    if cse_cx:
        data["api_keys"]["google_cse_cx"] = cse_cx
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if bot_token:
        data["api_keys"]["telegram_bot_token"] = bot_token

    # Migrate channels.json
    channels_file = BASE_DIR / "channels.json"
    if channels_file.exists():
        try:
            with open(channels_file, "r", encoding="utf-8") as f:
                channels = json.load(f)
            for ch in channels:
                merged = {**DEFAULT_CHANNEL, **ch}
                data["channels"].append(merged)
        except Exception:
            pass

    _write_settings(data)
    return data


# ── Public API ──────────────────────────────────────────────────────────


def get_settings() -> dict:
    """Get the full settings dict."""
    return _read_settings()


def save_settings(data: dict) -> None:
    """Save the full settings dict."""
    _write_settings(data)


# ── Channel CRUD ────────────────────────────────────────────────────────


def list_channels() -> list[dict]:
    """Return all channels."""
    return _read_settings().get("channels", [])


def get_channel(slug: str) -> Optional[dict]:
    """Get a channel by slug."""
    for ch in list_channels():
        if ch["slug"] == slug:
            return ch
    return None


def add_channel(channel_data: dict) -> dict:
    """Add a new channel. Returns the created channel dict."""
    data = _read_settings()

    # Merge with defaults
    ch = {**DEFAULT_CHANNEL, **channel_data}

    # Generate slug from name if not provided
    if not ch["slug"]:
        ch["slug"] = re.sub(r"[^a-z0-9]+", "_", ch["name"].lower()).strip("_")

    # Check for duplicate slug
    existing_slugs = {c["slug"] for c in data["channels"]}
    if ch["slug"] in existing_slugs:
        counter = 2
        while f"{ch['slug']}_{counter}" in existing_slugs:
            counter += 1
        ch["slug"] = f"{ch['slug']}_{counter}"

    # Create channel assets directory
    ch_dir = CHANNELS_DIR / ch["slug"]
    ch_dir.mkdir(parents=True, exist_ok=True)

    data["channels"].append(ch)
    _write_settings(data)
    return ch


def update_channel(slug: str, updates: dict) -> Optional[dict]:
    """Update a channel's settings. Returns updated channel or None."""
    data = _read_settings()
    for i, ch in enumerate(data["channels"]):
        if ch["slug"] == slug:
            # Don't allow slug change
            updates.pop("slug", None)
            data["channels"][i] = {**ch, **updates}
            _write_settings(data)
            return data["channels"][i]
    return None


def delete_channel(slug: str) -> bool:
    """Delete a channel and its assets. Returns True if deleted."""
    data = _read_settings()
    original_len = len(data["channels"])
    data["channels"] = [ch for ch in data["channels"] if ch["slug"] != slug]
    if len(data["channels"]) < original_len:
        _write_settings(data)
        # Optionally remove assets dir
        ch_dir = CHANNELS_DIR / slug
        if ch_dir.exists():
            shutil.rmtree(ch_dir, ignore_errors=True)
        return True
    return False


def save_channel_template(slug: str, image_bytes: bytes) -> str:
    """Save a template image for a channel. Returns the file path."""
    ch_dir = CHANNELS_DIR / slug
    ch_dir.mkdir(parents=True, exist_ok=True)
    template_path = ch_dir / "template.png"
    template_path.write_bytes(image_bytes)
    return str(template_path)


def save_channel_svg_template(slug: str, svg_bytes: bytes) -> str:
    """Save an SVG template for a channel. Returns the file path."""
    ch_dir = CHANNELS_DIR / slug
    ch_dir.mkdir(parents=True, exist_ok=True)
    svg_path = ch_dir / "template.svg"
    svg_path.write_bytes(svg_bytes)
    return str(svg_path)


def save_channel_logo(slug: str, image_bytes: bytes) -> str:
    """Save a logo image for a channel. Returns the file path."""
    ch_dir = CHANNELS_DIR / slug
    ch_dir.mkdir(parents=True, exist_ok=True)
    logo_path = ch_dir / "logo.png"
    logo_path.write_bytes(image_bytes)
    return str(logo_path)


# ── API Key Management ──────────────────────────────────────────────────


def get_api_keys(service: str) -> dict:
    """Get API keys + cycling toggle for a service."""
    data = _read_settings()
    api_keys = data.get("api_keys", {})
    if service in ("google_cse_cx", "telegram_bot_token"):
        return {"value": api_keys.get(service, "")}
    return api_keys.get(service, {"keys": [], "cycling": False})


def add_api_key(service: str, key: str) -> list[str]:
    """Add an API key for a service. Returns updated key list."""
    data = _read_settings()
    if service in ("google_cse_cx", "telegram_bot_token"):
        data["api_keys"][service] = key
        _write_settings(data)
        return [key]
    if service not in data["api_keys"]:
        data["api_keys"][service] = {"keys": [], "cycling": False}
    if key not in data["api_keys"][service]["keys"]:
        data["api_keys"][service]["keys"].append(key)
    _write_settings(data)
    return data["api_keys"][service]["keys"]


def remove_api_key(service: str, key_index: int) -> list[str]:
    """Remove an API key by index. Returns updated key list."""
    data = _read_settings()
    if service in data["api_keys"] and service != "google_cse_cx":
        keys = data["api_keys"][service]["keys"]
        if 0 <= key_index < len(keys):
            keys.pop(key_index)
            _write_settings(data)
        return keys
    return []


def set_cycling(service: str, enabled: bool) -> None:
    """Enable or disable key cycling for a service."""
    data = _read_settings()
    if service in data["api_keys"] and service != "google_cse_cx":
        data["api_keys"][service]["cycling"] = enabled
        _write_settings(data)


# ── Music Files ─────────────────────────────────────────────────────────

SUPPORTED_AUDIO = {".mp3", ".wav", ".ogg", ".m4a", ".aac"}


def list_music_files() -> list[str]:
    """List available music files in assets/music/."""
    return [
        f.name
        for f in MUSIC_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_AUDIO
    ]


# ── Cron Jobs ──────────────────────────────────────────────────────────


def list_cron_jobs() -> list[dict]:
    """Return all cron jobs."""
    return _read_settings().get("cron_jobs", [])


def get_cron_job(job_id: str) -> Optional[dict]:
    """Get a cron job by ID."""
    for job in list_cron_jobs():
        if job["id"] == job_id:
            return job
    return None


def add_cron_job(job_data: dict) -> dict:
    """Add a new cron job. Returns the created job dict."""
    import uuid
    data = _read_settings()
    if "cron_jobs" not in data:
        data["cron_jobs"] = []

    job = {
        "id": str(uuid.uuid4())[:8],
        "channel_slug": job_data.get("channel_slug", ""),
        "num_ideas": int(job_data.get("num_ideas", 10)),
        "schedule_time": job_data.get("schedule_time", "09:00"),
        "timezone": job_data.get("timezone", "Africa/Cairo"),
        "enabled": job_data.get("enabled", True),
        "telegram_chat_id": job_data.get("telegram_chat_id"),
    }
    data["cron_jobs"].append(job)
    _write_settings(data)
    return job


def update_cron_job(job_id: str, updates: dict) -> Optional[dict]:
    """Update a cron job's settings."""
    data = _read_settings()
    for i, job in enumerate(data.get("cron_jobs", [])):
        if job["id"] == job_id:
            updates.pop("id", None)
            data["cron_jobs"][i] = {**job, **updates}
            _write_settings(data)
            return data["cron_jobs"][i]
    return None


def delete_cron_job(job_id: str) -> bool:
    """Delete a cron job."""
    data = _read_settings()
    original = len(data.get("cron_jobs", []))
    data["cron_jobs"] = [j for j in data.get("cron_jobs", []) if j["id"] != job_id]
    if len(data["cron_jobs"]) < original:
        _write_settings(data)
        return True
    return False


# ── Team Members ──────────────────────────────────────────────────────


def list_team_members() -> list[dict]:
    """Return all team members."""
    return _read_settings().get("team_members", [])


def add_team_member(name: str, chat_id: int) -> dict:
    """Add a team member."""
    data = _read_settings()
    if "team_members" not in data:
        data["team_members"] = []
    member = {"name": name, "chat_id": chat_id}
    data["team_members"].append(member)
    _write_settings(data)
    return member


def delete_team_member(chat_id: int) -> bool:
    """Remove a team member by chat_id."""
    data = _read_settings()
    original = len(data.get("team_members", []))
    data["team_members"] = [
        m for m in data.get("team_members", []) if m["chat_id"] != chat_id
    ]
    if len(data["team_members"]) < original:
        _write_settings(data)
        return True
    return False


def get_team_member_name(chat_id: int) -> str:
    """Get a team member's name by chat_id."""
    for m in list_team_members():
        if m["chat_id"] == chat_id:
            return m["name"]
    return str(chat_id)

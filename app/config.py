"""
Configuration module — loads settings from settings_store and .env.

.env is only used for startup-time settings (bot token, base URL, bot mode).
All other config (channels, API keys) is managed via the admin UI and stored in data/settings.json.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets"
CHANNELS_DIR = ASSETS_DIR / "channels"
MUSIC_DIR = ASSETS_DIR / "music"
FONTS_DIR = ASSETS_DIR / "fonts"
BACKGROUNDS_DIR = ASSETS_DIR / "backgrounds"
OUTPUT_DIR = BASE_DIR / "output"

# Ensure directories exist
for d in [CHANNELS_DIR, MUSIC_DIR, FONTS_DIR, BACKGROUNDS_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Video constants ────────────────────────────────────────────────────
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 30

# ── Card margin (gap between card and video edges) ────────────────────
CARD_MARGIN = 50  # px of background visible around the card


# ── Channel config ─────────────────────────────────────────────────────
@dataclass
class ChannelConfig:
    name: str
    slug: str
    primary_color: str = "#1a1a2e"
    accent_color: str = "#e94560"
    text_color: str = "#ffffff"
    description: str = ""  # Channel style guide for AI
    card_mode: str = "pillow"  # "pillow", "ai", or "svg"
    sound_mode: str = "random"  # "random", "none", "specific"
    sound_file: Optional[str] = None
    video_duration: int = 5
    logo_path: Optional[str] = None
    template_path: Optional[str] = None
    svg_template_path: Optional[str] = None

    def __post_init__(self):
        channel_dir = CHANNELS_DIR / self.slug
        # Auto-resolve logo
        logo = channel_dir / "logo.png"
        if logo.exists():
            self.logo_path = str(logo)
        # Auto-resolve card template (PNG)
        template = channel_dir / "template.png"
        if template.exists():
            self.template_path = str(template)
        # Auto-resolve SVG template
        svg_template = channel_dir / "template.svg"
        if svg_template.exists():
            self.svg_template_path = str(svg_template)


def load_channels() -> list[ChannelConfig]:
    """Load channels from settings store."""
    from app.settings_store import list_channels as _list
    channels = []
    for item in _list():
        try:
            channels.append(ChannelConfig(**{
                k: v for k, v in item.items()
                if k in ChannelConfig.__dataclass_fields__
            }))
        except Exception:
            pass
    return channels


def get_channel(slug: str) -> Optional[ChannelConfig]:
    """Get a channel by its slug."""
    for ch in load_channels():
        if ch.slug == slug:
            return ch
    return None


# ── Startup Settings (from .env only) ─────────────────────────────────
@dataclass
class StartupSettings:
    telegram_bot_token: str = ""
    base_url: str = "http://localhost:8000"
    bot_mode: str = "polling"

    def __post_init__(self):
        self.base_url = os.getenv("BASE_URL", self.base_url)
        self.bot_mode = os.getenv("BOT_MODE", self.bot_mode)
        # Bot token: try settings store first, then .env
        try:
            from app.settings_store import get_api_keys
            stored = get_api_keys("telegram_bot_token")
            if stored.get("value"):
                self.telegram_bot_token = stored["value"]
        except Exception:
            pass
        if not self.telegram_bot_token:
            self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")


settings = StartupSettings()

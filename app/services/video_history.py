"""
Video History — tracks past video facts per channel to avoid duplicate ideas.
Stored in data/video_history.json.
"""

import json
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
HISTORY_FILE = BASE_DIR / "data" / "video_history.json"


def _read_history() -> dict:
    """Read history from disk."""
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _write_history(data: dict) -> None:
    """Write history to disk."""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def add_to_history(channel_slug: str, fact) -> None:
    """Add a generated fact to channel history."""
    data = _read_history()
    if channel_slug not in data:
        data[channel_slug] = []

    entry = {
        "title": fact.title,
        "body": fact.body,
        "yt_title": getattr(fact, "yt_title", ""),
        "timestamp": datetime.now().isoformat(),
    }
    data[channel_slug].append(entry)

    # Keep last 200 per channel
    if len(data[channel_slug]) > 200:
        data[channel_slug] = data[channel_slug][-200:]

    _write_history(data)


def get_history(channel_slug: str, limit: int = 20) -> list[dict]:
    """Get recent video history for a channel."""
    data = _read_history()
    entries = data.get(channel_slug, [])
    return entries[-limit:]


def get_past_titles(channel_slug: str, limit: int = 30) -> list[str]:
    """Get past video titles for duplicate avoidance."""
    entries = get_history(channel_slug, limit)
    return [e["title"] for e in entries if e.get("title")]

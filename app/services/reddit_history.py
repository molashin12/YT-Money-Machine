"""
Reddit History — tracks past scraped reddit posts to avoid duplicates.
Stored in data/reddit_history.json.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
HISTORY_FILE = BASE_DIR / "data" / "reddit_history.json"


def _read_history() -> dict:
    """Read history from disk."""
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read reddit history: {e}")
    return {"seen_posts": []}


def _write_history(data: dict) -> None:
    """Write history to disk."""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to write reddit history: {e}")


def is_post_seen(post_id: str) -> bool:
    """Check if a reddit post has been processed before."""
    data = _read_history()
    return post_id in data.get("seen_posts", [])


def mark_post_seen(post_id: str) -> None:
    """Mark a reddit post as processed. Keeps the last 5000 IDs to avoid unbounded growth."""
    data = _read_history()
    seen = data.get("seen_posts", [])
    
    if post_id not in seen:
        seen.append(post_id)
        
        # Limit history size to prevent file bloat
        if len(seen) > 5000:
            seen = seen[-5000:]
            
        data["seen_posts"] = seen
        _write_history(data)

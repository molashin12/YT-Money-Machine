"""
Music Selector — picks music based on channel preferences.

Sound modes:
- "random": pick a random file from assets/music/
- "specific": use the channel's chosen file
- "none": no music
"""

import logging
import random
from pathlib import Path
from typing import Optional

from app.config import MUSIC_DIR

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".aac"}


def select_music(sound_mode: str = "random", sound_file: Optional[str] = None) -> Optional[str]:
    """
    Select music based on channel preferences.

    Args:
        sound_mode: "random", "specific", or "none"
        sound_file: filename in assets/music/ (used when sound_mode="specific")

    Returns:
        File path to selected music, or None for no music.
    """
    if sound_mode == "none":
        logger.info("Sound mode: none — no music")
        return None

    if sound_mode == "specific" and sound_file:
        specific_path = MUSIC_DIR / sound_file
        if specific_path.exists():
            logger.info(f"Sound mode: specific — {sound_file}")
            return str(specific_path)
        else:
            logger.warning(f"Specific music file not found: {sound_file}, falling back to random")

    # Random mode (or fallback)
    music_files = [
        f for f in MUSIC_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not music_files:
        logger.warning(
            f"No music files found in {MUSIC_DIR}. "
            "Add .mp3/.wav files to assets/music/ for background music."
        )
        return None

    choice = random.choice(music_files)
    logger.info(f"Sound mode: random — {choice.name}")
    return str(choice)

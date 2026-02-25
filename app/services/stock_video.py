"""
Stock Video — fetches calm background videos from Pexels API.
Caches downloads to avoid re-fetching.
"""

import logging
import random
import shutil
from pathlib import Path
from typing import Optional

import httpx

from app.config import BACKGROUNDS_DIR, CHANNELS_DIR
from app.services.api_key_manager import get_key_manager

logger = logging.getLogger(__name__)

SEARCH_QUERIES = [
    "calm nature",
    "flowers blooming",
    "peaceful water",
    "soft clouds sky",
    "gentle waves",
    "green leaves",
    "sunset calm",
    "morning dew",
    "lavender field",
    "cherry blossom",
]


async def fetch_background_video(channel_slug: str) -> Optional[str]:
    """
    Fetch a calming background video from Pexels.
    Caches it per-channel to ensure consistency.
    Returns the file path to the downloaded video.
    """
    channel_dir = CHANNELS_DIR / channel_slug
    channel_dir.mkdir(parents=True, exist_ok=True)
    channel_bg = channel_dir / "background.mp4"

    # 1. If this channel already has a cached background, always use it
    if channel_bg.exists():
        logger.info(f"Using channel cached background: {channel_bg}")
        return str(channel_bg)

    # 2. Check if we already have global cached background videos
    cached = list(BACKGROUNDS_DIR.glob("*.mp4"))
    if cached and len(cached) >= 3:
        choice = random.choice(cached)
        logger.info(f"Using globally cached background: {choice.name}")
        shutil.copy2(choice, channel_bg)
        return str(channel_bg)

    # 3. Fetch a new video from Pexels
    manager = get_key_manager()
    pexels_key = manager.get_pexels_key()
    if not pexels_key:
        logger.warning("No Pexels API key configured")
        return _fallback_cached(channel_bg)

    query = random.choice(SEARCH_QUERIES)
    logger.info(f"Fetching background video from Pexels: '{query}'")

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                "https://api.pexels.com/videos/search",
                params={
                    "query": query,
                    "orientation": "portrait",
                    "size": "medium",
                    "per_page": 15,
                },
                headers={"Authorization": pexels_key},
            )
            resp.raise_for_status()
            data = resp.json()

        videos = data.get("videos", [])
        if not videos:
            logger.warning("No videos found on Pexels")
            return _fallback_cached(channel_bg)

        # Pick a random video from results
        random.shuffle(videos)

        for video in videos:
            video_files = video.get("video_files", [])
            # Prefer HD portrait files
            suitable = [
                vf for vf in video_files
                if vf.get("height", 0) >= 1080
                and vf.get("width", 0) <= vf.get("height", 0)  # portrait
                and vf.get("file_type") == "video/mp4"
            ]
            if not suitable:
                # Accept any MP4
                suitable = [
                    vf for vf in video_files
                    if vf.get("file_type") == "video/mp4"
                ]
            if not suitable:
                continue

            # Pick the best quality
            suitable.sort(key=lambda x: x.get("height", 0), reverse=True)
            video_url = suitable[0].get("link")
            if not video_url:
                continue

            # Download
            logger.info(f"Downloading video: {video_url}")
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                dl_resp = await client.get(video_url)
                if dl_resp.status_code == 200:
                    channel_bg.write_bytes(dl_resp.content)
                    
                    # Also save to global cache
                    video_id = video.get("id", random.randint(1000, 9999))
                    output_path = BACKGROUNDS_DIR / f"pexels_{video_id}.mp4"
                    shutil.copy2(channel_bg, output_path)

                    logger.info(f"Saved background video: {channel_bg}")
                    return str(channel_bg)

        logger.warning("Could not download any Pexels video")
        return _fallback_cached(channel_bg)

    except Exception as e:
        logger.error(f"Pexels API error: {e}")
        return _fallback_cached(channel_bg)


def _fallback_cached(channel_bg: Path) -> Optional[str]:
    """Fall back to any cached background video."""
    cached = list(BACKGROUNDS_DIR.glob("*.mp4"))
    if cached:
        choice = random.choice(cached)
        shutil.copy2(choice, channel_bg)
        return str(channel_bg)
    return None

"""
Pipeline Orchestrator — ties all services together into a single flow:

Input → Content Extraction (merged with fact for images/URLs)
     → Image Search → Card Building → Video Assembly → Output

Returns both the video path AND YouTube metadata (title, description, hashtags).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.config import ChannelConfig, get_channel
from app.services.content_extractor import (
    detect_input_type,
    extract_from_url,
    extract_from_image,
    extract_from_text,
)
from app.services.fact_extractor import extract_facts, ExtractedFact
from app.services.image_search import search_image
from app.services.stock_video import fetch_background_video
from app.services.music_selector import select_music
from app.services.video_assembler import assemble_video

logger = logging.getLogger(__name__)


@dataclass
class VideoResult:
    """Result of the video generation pipeline."""
    video_path: str
    yt_title: str = ""
    yt_description: str = ""
    yt_hashtags: list[str] = field(default_factory=list)
    fact_title: str = ""
    fact_body: str = ""

    @property
    def hashtags_str(self) -> str:
        return " ".join(self.yt_hashtags)

    def to_dict(self) -> dict:
        return {
            "video_path": self.video_path,
            "yt_title": self.yt_title,
            "yt_description": self.yt_description,
            "yt_hashtags": self.yt_hashtags,
            "fact_title": self.fact_title,
            "fact_body": self.fact_body,
        }


async def generate_video(
    channel_slug: str,
    text: str = "",
    image_bytes: Optional[bytes] = None,
    progress_callback=None,
    fact_override: Optional[ExtractedFact] = None,
) -> Optional[VideoResult]:
    """
    Main pipeline: from input (text/image/URL) to a finished video + YouTube metadata.

    Args:
        fact_override: If provided, skip extraction and use this fact directly
                       (used by cron job system for pre-generated ideas).
    """
    channel = get_channel(channel_slug)
    if not channel:
        logger.error(f"Channel not found: {channel_slug}")
        return None

    async def _progress(msg: str):
        logger.info(f"[Pipeline] {msg}")
        if progress_callback:
            await progress_callback(msg)

    try:
        # ── Step 1+2: Extract content + facts ──────────────────────────
        if fact_override:
            # Cron job path: fact already provided
            fact = fact_override
            logger.info(f"Using pre-generated fact: {fact.title}")
        else:
            await _progress("📥 Analyzing input content...")
            channel_desc = getattr(channel, "description", "")
            input_type = detect_input_type(text, has_image=image_bytes is not None)

            if input_type == "url":
                extracted = await extract_from_url(text.strip(), channel_desc)
            elif input_type == "image":
                extracted = await extract_from_image(image_bytes, channel_description=channel_desc)
            elif input_type == "text_image":
                extracted = await extract_from_image(
                    image_bytes, caption=text, channel_description=channel_desc
                )
            else:
                extracted = await extract_from_text(text)

            # Check if merged extraction already gave us a fact
            fact = extracted.get("fact")

            if not fact:
                raw_content = extracted.get("raw_text", "")
                if not raw_content:
                    logger.error("No content could be extracted")
                    return None
                # Separate fact extraction (text inputs, or if merged failed)
                await _progress("🧠 Extracting facts with AI...")
                fact = await extract_facts(raw_content, channel_desc)

        logger.info(f"Fact: {fact.title} — {fact.body}")
        if fact.yt_title:
            logger.info(f"YT Title: {fact.yt_title}")

        # ── Step 3: Search/generate related image ──────────────────────
        await _progress("🔍 Finding a related image...")
        image_result = await search_image(fact.keywords)
        related_image_bytes = image_result.image_bytes if image_result else None
        image_source = image_result.source if image_result else ""

        # ── Step 4: Build card image ───────────────────────────────────
        card_mode = getattr(channel, "card_mode", "pillow")

        if card_mode == "ai":
            await _progress("🎨 Editing card template with AI...")
            from app.services.card_builder import build_card
            card_bytes = await build_card(
                channel=channel,
                title=fact.title,
                body=fact.body,
                related_image=related_image_bytes,
                image_source=image_source,
            )
        else:
            await _progress("🎨 Building card with Pillow...")
            from app.services.card_builder_pillow import build_card_pillow
            card_bytes = build_card_pillow(
                channel=channel,
                title=fact.title,
                body=fact.body,
                related_image=related_image_bytes,
                image_source=image_source,
            )

        if not card_bytes:
            logger.error("Card building failed")
            return None

        # ── Step 5: Fetch background video ─────────────────────────────
        await _progress("🎬 Fetching background video...")
        bg_video_path = await fetch_background_video()
        if not bg_video_path:
            logger.error("No background video available")
            return None

        # ── Step 6: Select music (based on channel preference) ──────────
        await _progress("🎵 Selecting background music...")
        music_path = select_music(
            sound_mode=channel.sound_mode,
            sound_file=channel.sound_file,
        )

        # ── Step 7: Assemble video ─────────────────────────────────────
        await _progress("🎞️ Assembling the final video...")
        video_path = assemble_video(
            card_image_bytes=card_bytes,
            background_video_path=bg_video_path,
            music_path=music_path,
            duration=channel.video_duration,
        )

        if video_path:
            # Save to video history
            try:
                from app.services.video_history import add_to_history
                add_to_history(channel_slug, fact)
            except Exception as e:
                logger.warning(f"Failed to save to history: {e}")

            await _progress("✅ Video ready!")
            return VideoResult(
                video_path=video_path,
                yt_title=fact.yt_title,
                yt_description=fact.yt_description,
                yt_hashtags=fact.yt_hashtags,
                fact_title=fact.title,
                fact_body=fact.body,
            )
        else:
            await _progress("❌ Video assembly failed")
            return None

    except Exception as e:
        logger.exception(f"Pipeline failed: {e}")
        await _progress(f"❌ Error: {str(e)}")
        return None

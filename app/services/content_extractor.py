"""
Content Extractor — handles input parsing from URLs, images, and text.
Uses yt-dlp for social media links and Gemini for image analysis.

OPTIMIZED: For image/URL inputs, performs content analysis AND fact extraction
in a single Gemini call (merged extraction) to save tokens.
"""

import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Optional

from google.genai import types as genai_types

from app.services.api_key_manager import get_key_manager
from app.services.fact_extractor import ExtractedFact

logger = logging.getLogger(__name__)

# URL patterns for supported platforms
URL_PATTERNS = {
    "tiktok": re.compile(r"(https?://)?(www\.|vm\.)?tiktok\.com/", re.IGNORECASE),
    "instagram": re.compile(r"(https?://)?(www\.)?instagram\.com/(reel|p)/", re.IGNORECASE),
    "facebook": re.compile(r"(https?://)?(www\.|m\.)?facebook\.com/.*(reel|video)", re.IGNORECASE),
    "youtube": re.compile(r"(https?://)?(www\.)?(youtube\.com/shorts|youtu\.be)/", re.IGNORECASE),
}

# Combined prompt: analyze image AND extract fact in one call
MERGED_EXTRACTION_PROMPT = """Analyze this image carefully and extract one interesting, surprising, or educational fact from it.
If there's text in the image, read and use it. If in another language, translate to English.

{channel_context}
{additional_context}

Return ONLY JSON:
{{"title":"Short catchy headline (3-6 words)","body":"The fact in 1-3 punchy sentences, max 200 chars","keywords":["visual_keyword1","visual_keyword2","visual_keyword3"],"yt_title":"Catchy YouTube Shorts title (max 70 chars, include emoji)","yt_description":"Brief YouTube description (2-3 sentences, include call to action)","yt_hashtags":["#tag1","#tag2","#tag3","#tag4","#tag5"]}}"""


def detect_input_type(text: str, has_image: bool = False) -> str:
    """Detect input type: 'url', 'image', 'text_image', or 'text'."""
    text = text.strip() if text else ""
    if has_image and text:
        return "text_image"
    if has_image:
        return "image"
    for platform, pattern in URL_PATTERNS.items():
        if pattern.search(text):
            return "url"
    return "text"


async def extract_from_url(url: str, channel_description: str = "") -> dict:
    """
    Download video from social media URL using yt-dlp, extract a frame,
    and use Gemini to analyze + extract fact in one call.
    """
    import yt_dlp

    logger.info(f"Extracting content from URL: {url}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Download video
        ydl_opts = {
            "outtmpl": f"{tmp_dir}/%(id)s.%(ext)s",
            # Fallback chain: try 720p → best single file → absolute best
            "format": "best[height<=720]/best/bestvideo+bestaudio",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": "mp4",
        }

        video_path = None
        video_info = {}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_info = {
                "title": info.get("title", ""),
                "description": info.get("description", ""),
            }
            for f in Path(tmp_dir).iterdir():
                if f.is_file():
                    video_path = f
                    break

        raw_text = f"Title: {video_info.get('title', '')}\nDescription: {video_info.get('description', '')}"

        if not video_path:
            return {"raw_text": raw_text, "source": "url"}

        # Extract a frame from the video using FFmpeg
        frame_path = Path(tmp_dir) / "frame.jpg"
        import subprocess

        subprocess.run(
            [
                "ffmpeg", "-i", str(video_path),
                "-vf", "select=eq(n\\,30)",
                "-vframes", "1",
                "-q:v", "2",
                str(frame_path),
            ],
            capture_output=True,
            timeout=30,
        )

        if frame_path.exists():
            frame_bytes = frame_path.read_bytes()
            # Merged: analyze image + extract fact in one call
            fact = await _merged_analyze_and_extract(
                frame_bytes, raw_text, channel_description
            )
            if fact:
                return {"raw_text": raw_text, "source": "url", "fact": fact}

        return {"raw_text": raw_text, "source": "url"}


async def extract_from_image(
    image_bytes: bytes, caption: str = "", channel_description: str = ""
) -> dict:
    """Merged: analyze image + extract fact in one Gemini call."""
    logger.info("Extracting content from image (merged)")
    fact = await _merged_analyze_and_extract(
        image_bytes, caption, channel_description
    )
    if fact:
        return {"raw_text": caption or fact.body, "source": "image", "fact": fact}
    return {"raw_text": caption, "source": "image"}


async def extract_from_text(text: str) -> dict:
    """Pass text through directly (fact extraction happens separately)."""
    return {"raw_text": text, "source": "text"}


async def _merged_analyze_and_extract(
    image_bytes: bytes,
    additional_context: str = "",
    channel_description: str = "",
) -> Optional[ExtractedFact]:
    """
    Single Gemini call: analyzes the image AND extracts structured fact + YT metadata.
    Saves one full API call compared to analyze-then-extract.
    """
    try:
        manager = get_key_manager()

        channel_context = ""
        if channel_description:
            channel_context = f"CHANNEL STYLE: {channel_description}"

        ctx = ""
        if additional_context:
            ctx = f"Context: {additional_context}"

        prompt = MERGED_EXTRACTION_PROMPT.format(
            channel_context=channel_context,
            additional_context=ctx,
        )

        response = await manager.gemini_generate(
            model="gemini-2.5-flash",
            contents=[
                genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                prompt,
            ],
            config=genai_types.GenerateContentConfig(
                temperature=0.7,
                response_mime_type="application/json",
            ),
        )

        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            text = text.rsplit("```", 1)[0]

        data = json.loads(text)

        return ExtractedFact(
            title=data.get("title", "Did You Know?"),
            body=data.get("body", ""),
            keywords=data.get("keywords", ["interesting", "facts"]),
            yt_title=data.get("yt_title", ""),
            yt_description=data.get("yt_description", ""),
            yt_hashtags=data.get("yt_hashtags", []),
        )
    except Exception as e:
        logger.error(f"Merged extraction failed: {e}")
        return None

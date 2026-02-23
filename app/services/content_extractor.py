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
from app.services.fact_extractor import ExtractedFact, _enforce_body_length

logger = logging.getLogger(__name__)

# URL patterns for supported platforms
URL_PATTERNS = {
    "tiktok": re.compile(r"(https?://)?(www\.|vm\.)?tiktok\.com/", re.IGNORECASE),
    "instagram": re.compile(r"(https?://)?(www\.)?instagram\.com/(reel|p)/", re.IGNORECASE),
    "facebook": re.compile(r"(https?://)?(www\.|m\.)?facebook\.com/.*(reel|video)", re.IGNORECASE),
    "youtube": re.compile(r"(https?://)?(www\.)?(youtube\.com/shorts|youtu\.be)/", re.IGNORECASE),
}

MERGED_EXTRACTION_PROMPT = """Analyze this image carefully and extract one interesting, surprising, or educational fact from it.
If there's text in the image, read and use it. If in another language, translate to English.

{channel_context}
{additional_context}

Return ONLY JSON:
{{"title":"Short catchy headline (3-6 words)","body":"The main fact text — MUST be between 40 and 50 words long. Count carefully.","keywords":["keyword1","keyword2","keyword3","keyword4","keyword5"],"image_search_query":"Best 3-5 word search to find a PHOTO of the main subject (use person names, place names, or specific objects)","yt_title":"Catchy YouTube Shorts title (max 70 chars, include emoji)","yt_description":"Brief YouTube description (2-3 sentences, include call to action)","yt_hashtags":["#tag1","#tag2","#tag3","#tag4","#tag5"]}}

CRITICAL: The "body" MUST be between 40 and 50 words. Count every word carefully. If too long, shorten it. If too short, add descriptive details.

KEYWORD RULES: If about a PERSON, first keyword = their full name. If about a PLACE, include the place name. Keywords must be specific and searchable."""


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
    Download video from social media URL, extract a frame,
    and use Gemini to analyze + extract fact in one call.

    Download strategies (tries in order):
    1. yt-dlp with login-bypass options
    2. Instaloader (Instagram only — works without login)
    3. yt-dlp with transformed URL (embed/mobile)
    4. Cobalt API (open-source video downloader)
    """
    logger.info(f"Extracting content from URL: {url}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        video_path = None
        video_title = ""
        video_desc = ""

        # ── Strategy 1: yt-dlp with bypass options ──
        video_path, video_title, video_desc = await _download_ytdlp(url, tmp_dir)

        # ── Strategy 2: Instaloader (Instagram only) ──
        if not video_path and "instagram.com" in url:
            video_path = await _download_instaloader(url, tmp_dir)

        # ── Strategy 3: yt-dlp with transformed URL ──
        if not video_path:
            alt_url = _transform_url(url)
            if alt_url and alt_url != url:
                logger.info(f"Trying transformed URL: {alt_url}")
                video_path, video_title, video_desc = await _download_ytdlp(alt_url, tmp_dir)

        # ── Strategy 4: Cobalt API ──
        if not video_path:
            video_path = await _download_cobalt(url, tmp_dir)

        # ── Build raw text from metadata ──
        raw_text = ""
        if video_title:
            raw_text += f"Title: {video_title}\n"
        if video_desc:
            raw_text += f"Description: {video_desc}"
        raw_text = raw_text.strip()

        # If no video downloaded, try OG metadata fallback for text
        if not video_path:
            logger.warning("All download strategies failed — falling back to OG metadata")
            og_text = await _scrape_og_metadata(url)
            return {"raw_text": og_text or f"Content from URL: {url}", "source": "url"}

        logger.info(f"Video downloaded: {video_path}")

        # If we don't have metadata yet, try OG scrape for context
        if not raw_text:
            og_text = await _scrape_og_metadata(url)
            raw_text = og_text or f"Content from URL: {url}"

        # ── Extract frame from video using FFmpeg ──
        frame_path = Path(tmp_dir) / "frame.jpg"
        import subprocess

        try:
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
        except Exception as e:
            logger.warning(f"FFmpeg frame extraction failed: {e}")

        if frame_path.exists():
            frame_bytes = frame_path.read_bytes()
            # Merged: analyze image + extract fact in one call
            fact = await _merged_analyze_and_extract(
                frame_bytes, raw_text, channel_description
            )
            if fact:
                return {"raw_text": raw_text, "source": "url", "fact": fact}

        return {"raw_text": raw_text, "source": "url"}


async def _download_ytdlp(url: str, tmp_dir: str) -> tuple:
    """Download video with yt-dlp. Returns (video_path, title, description)."""
    try:
        import yt_dlp

        ydl_opts = {
            "outtmpl": f"{tmp_dir}/%(id)s.%(ext)s",
            "format": "best[height<=720]/best/bestvideo+bestaudio",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": "mp4",
            "socket_timeout": 30,
            "retries": 2,
            # Bypass login requirements
            "extractor_args": {
                "instagram": {"skip": ["login"]},
                "facebook": {"skip": ["login"]},
            },
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
            },
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "")
            desc = info.get("description", "")

            # Find downloaded file
            for f in Path(tmp_dir).iterdir():
                if f.is_file() and f.suffix in (".mp4", ".webm", ".mkv", ".mov"):
                    logger.info(f"yt-dlp downloaded: {f.name}")
                    return (f, title, desc)

    except Exception as e:
        logger.warning(f"yt-dlp download failed: {e}")

    return (None, "", "")


async def _download_instaloader(url: str, tmp_dir: str) -> Optional[Path]:
    """Download Instagram video/reel using Instaloader (no login needed for public posts)."""
    try:
        import instaloader

        logger.info("Trying Instaloader for Instagram download...")

        # Extract shortcode from URL
        ig_match = re.search(r"instagram\.com/(?:reel|p)/([A-Za-z0-9_-]+)", url)
        if not ig_match:
            logger.warning("Could not extract Instagram shortcode from URL")
            return None

        shortcode = ig_match.group(1)
        logger.info(f"Instagram shortcode: {shortcode}")

        # Create Instaloader instance (no login)
        L = instaloader.Instaloader(
            download_videos=True,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
            dirname_pattern=tmp_dir,
            filename_pattern="{shortcode}",
            quiet=True,
        )

        # Download the post
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        L.download_post(post, target=Path(tmp_dir))

        # Find downloaded video file
        for f in Path(tmp_dir).rglob("*"):
            if f.is_file() and f.suffix in (".mp4", ".webm", ".mkv", ".mov"):
                logger.info(f"Instaloader downloaded: {f.name}")
                return f

        logger.warning("Instaloader completed but no video file found")

    except Exception as e:
        logger.warning(f"Instaloader download failed: {e}")

    return None


def _transform_url(url: str) -> Optional[str]:
    """Transform social media URLs to bypass login walls (embed/mobile variants)."""
    # Instagram: try embed URL
    ig_match = re.search(r"instagram\.com/(reel|p)/([A-Za-z0-9_-]+)", url)
    if ig_match:
        post_type, post_id = ig_match.groups()
        return f"https://www.instagram.com/{post_type}/{post_id}/embed/"

    # TikTok: try mobile/vm format
    tk_match = re.search(r"tiktok\.com/@[^/]+/video/(\d+)", url)
    if tk_match:
        video_id = tk_match.group(1)
        return f"https://vm.tiktok.com/{video_id}"

    # Facebook: try mbasic
    if "facebook.com" in url:
        return url.replace("www.facebook.com", "mbasic.facebook.com").replace("m.facebook.com", "mbasic.facebook.com")

    return None


async def _download_cobalt(url: str, tmp_dir: str) -> Optional[Path]:
    """Download video using Cobalt API (open-source, no login needed)."""
    try:
        import httpx

        logger.info("Trying Cobalt API for video download...")

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.cobalt.tools/",
                json={"url": url},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )

        if resp.status_code != 200:
            logger.warning(f"Cobalt API returned {resp.status_code}")
            return None

        data = resp.json()
        video_url = data.get("url")

        if not video_url:
            logger.warning("Cobalt API returned no video URL")
            return None

        # Download the video file
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            vid_resp = await client.get(video_url)

        if vid_resp.status_code == 200:
            video_path = Path(tmp_dir) / "cobalt_video.mp4"
            video_path.write_bytes(vid_resp.content)
            logger.info(f"Cobalt downloaded: {len(vid_resp.content)} bytes")
            return video_path

    except Exception as e:
        logger.warning(f"Cobalt download failed: {e}")

    return None


async def _scrape_og_metadata(url: str) -> Optional[str]:
    """
    Scrape Open Graph metadata from a public URL.
    Works for Instagram, TikTok, Facebook, YouTube — any page with og:tags.
    """
    try:
        import httpx

        logger.info(f"Scraping OG metadata from: {url}")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code != 200:
            return None

        html = resp.text

        og_title = _extract_meta(html, "og:title")
        og_desc = _extract_meta(html, "og:description")
        page_title = _extract_tag(html, "title")
        meta_desc = _extract_meta(html, "description")

        parts = []
        title = og_title or page_title or ""
        desc = og_desc or meta_desc or ""

        if title:
            parts.append(f"Title: {title}")
        if desc:
            parts.append(f"Description: {desc}")

        return "\n".join(parts) if parts else None

    except Exception as e:
        logger.warning(f"OG metadata scraping failed: {e}")
        return None


def _extract_meta(html: str, name: str) -> str:
    """Extract content from a meta tag (property or name)."""
    match = re.search(
        rf'<meta\s+(?:[^>]*\s)?(?:property|name)=["\'](?:og:)?{re.escape(name)}["\'][^>]*\s+content=["\']([^"\']*)["\']',
        html, re.IGNORECASE
    )
    if not match:
        match = re.search(
            rf'<meta\s+(?:[^>]*\s)?content=["\']([^"\']*)["\'][^>]*\s+(?:property|name)=["\'](?:og:)?{re.escape(name)}["\']',
            html, re.IGNORECASE
        )
    return match.group(1).strip() if match else ""


def _extract_tag(html: str, tag: str) -> str:
    """Extract text content from an HTML tag."""
    match = re.search(rf'<{tag}[^>]*>([^<]+)</{tag}>', html, re.IGNORECASE)
    return match.group(1).strip() if match else ""


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
            body=_enforce_body_length(data.get("body", "")),
            keywords=data.get("keywords", ["interesting", "facts"]),
            image_search_query=data.get("image_search_query", ""),
            yt_title=data.get("yt_title", ""),
            yt_description=data.get("yt_description", ""),
            yt_hashtags=data.get("yt_hashtags", []),
        )
    except Exception as e:
        logger.error(f"Merged extraction failed: {e}")
        return None

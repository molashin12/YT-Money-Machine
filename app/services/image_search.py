"""
Image Search — finds relevant, safe images using Google Custom Search
or falls back to Gemini image generation.

Returns both image bytes AND source attribution (IG: @user, domain, etc.).
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx
from google.genai import types as genai_types

from app.services.api_key_manager import get_key_manager

logger = logging.getLogger(__name__)


@dataclass
class ImageResult:
    """Image bytes + source attribution."""
    image_bytes: bytes
    source: str  # e.g. "source: IG: @username" or "source: wikipedia.org"


# Social media domain patterns → platform names
SOCIAL_PLATFORMS = {
    "instagram.com": "IG",
    "twitter.com": "X",
    "x.com": "X",
    "tiktok.com": "TikTok",
    "facebook.com": "Facebook",
    "pinterest.com": "Pinterest",
    "reddit.com": "Reddit",
    "tumblr.com": "Tumblr",
    "flickr.com": "Flickr",
    "youtube.com": "YouTube",
}


def _build_source_attribution(display_link: str, page_title: str = "", page_url: str = "") -> str:
    """
    Build a smart source attribution string.
    - Social media: "IG: @username", "TikTok: @username"
    - Websites: "source: domain.com"
    """
    if not display_link:
        return "source: web"

    domain = display_link.lower().replace("www.", "")

    # Check if it's a social media platform
    for social_domain, platform_name in SOCIAL_PLATFORMS.items():
        if social_domain in domain:
            # Try to extract username from the URL or title
            username = _extract_username(page_url, page_title, social_domain)
            if username:
                return f"source: {platform_name}: @{username}"
            else:
                return f"source: {platform_name}"

    # Regular website: use domain name
    return f"source: {domain}"


def _extract_username(url: str, title: str, domain: str) -> Optional[str]:
    """Try to extract a social media username from the URL or page title."""
    # Try URL path: e.g. instagram.com/username/...
    if url:
        try:
            parsed = urlparse(url)
            path_parts = [p for p in parsed.path.strip("/").split("/") if p]
            if path_parts:
                first = path_parts[0]
                # Skip common non-username paths
                skip = {"p", "reel", "reels", "explore", "stories", "tv",
                        "video", "watch", "status", "hashtag", "search", "photo"}
                if first.lower() not in skip and not first.startswith("_"):
                    # Clean up username
                    username = re.sub(r"[^a-zA-Z0-9_.]", "", first)
                    if username and len(username) <= 30:
                        return username
        except Exception:
            pass

    # Try title: sometimes contains "@username" patterns
    if title:
        at_match = re.search(r"@([a-zA-Z0-9_.]{1,30})", title)
        if at_match:
            return at_match.group(1)

    return None


async def search_image(keywords: list[str]) -> Optional[ImageResult]:
    """
    Search for a relevant image. Uses Google Custom Search if available,
    otherwise generates one with Gemini.
    """
    manager = get_key_manager()

    if manager.has_image_search():
        result = await _google_image_search(keywords, manager)
        if result:
            return result

    return await _generate_image_with_gemini(keywords, manager)


async def _google_image_search(keywords: list[str], manager) -> Optional[ImageResult]:
    """Search Google Custom Search Images API with SafeSearch on."""
    try:
        query = " ".join(keywords[:5])
        logger.info(f"Searching Google Images for: {query}")

        cse_key = manager.get_cse_key()
        cse_cx = manager.get_cse_cx()

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "key": cse_key,
                    "cx": cse_cx,
                    "q": query,
                    "searchType": "image",
                    "safe": "active",
                    "imgType": "photo",
                    "imgSize": "large",
                    "num": 5,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        items = data.get("items", [])
        if not items:
            logger.warning("No images found via Google CSE")
            return None

        for item in items:
            image_url = item.get("link", "")
            display_link = item.get("displayLink", "")
            page_title = item.get("title", "")
            page_url = item.get("image", {}).get("contextLink", "")
            if not image_url:
                continue
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                    img_resp = await client.get(image_url)
                    if img_resp.status_code == 200 and len(img_resp.content) > 1000:
                        source = _build_source_attribution(display_link, page_title, page_url)
                        logger.info(f"Downloaded image — {source}")
                        return ImageResult(image_bytes=img_resp.content, source=source)
            except Exception:
                continue

        return None
    except Exception as e:
        logger.error(f"Google image search failed: {e}")
        return None


async def _generate_image_with_gemini(keywords: list[str], manager) -> Optional[ImageResult]:
    """Generate a relevant image using Gemini 2.5 Flash."""
    try:
        query = ", ".join(keywords)
        logger.info(f"Generating image with Gemini for: {query}")

        prompt = (
            f"Generate a high-quality, photorealistic image showing: {query}. "
            "The image should be clean, professional, suitable for a YouTube video, "
            "and family-friendly. No text or watermarks in the image."
        )

        response = await manager.gemini_generate(
            model="gemini-2.5-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

        if response.candidates:
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                    return ImageResult(
                        image_bytes=part.inline_data.data,
                        source="source: AI generated",
                    )

        logger.warning("Gemini did not return an image")
        return None
    except Exception as e:
        logger.error(f"Gemini image generation failed: {e}")
        return None

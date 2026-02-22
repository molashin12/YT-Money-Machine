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
    Search for a relevant image. Tries multiple sources in order:
    1. Google Custom Search (if configured)
    2. Bing Image Search (no API key needed)
    3. Pexels Photos (if key available)
    4. Gemini image generation (last resort)
    """
    manager = get_key_manager()

    # 1. Google CSE
    if manager.has_image_search():
        result = await _google_image_search(keywords, manager)
        if result:
            return result
        logger.info("Google CSE failed, trying next source...")

    # 2. Bing (no API key needed)
    result = await _bing_image_search(keywords)
    if result:
        return result
    logger.info("Bing failed, trying next source...")

    # 3. Pexels Photos
    pexels_key = manager.get_pexels_key()
    if pexels_key:
        result = await _pexels_image_search(keywords, pexels_key)
        if result:
            return result
        logger.info("Pexels failed, trying Gemini...")

    # 4. Gemini generation (last resort)
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


async def _pexels_image_search(keywords: list[str], api_key: str) -> Optional[ImageResult]:
    """Search Pexels Photos API for relevant images."""
    try:
        query = " ".join(keywords[:5])
        logger.info(f"Searching Pexels photos for: {query}")

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://api.pexels.com/v1/search",
                params={"query": query, "per_page": 10, "size": "large"},
                headers={"Authorization": api_key},
            )
            resp.raise_for_status()
            data = resp.json()

        photos = data.get("photos", [])
        if not photos:
            return None

        import random
        random.shuffle(photos)

        for photo in photos[:5]:
            img_url = photo.get("src", {}).get("large2x") or photo.get("src", {}).get("large")
            if not img_url:
                continue
            try:
                photographer = photo.get("photographer", "Pexels")
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                    img_resp = await client.get(img_url)
                    if img_resp.status_code == 200 and len(img_resp.content) > 1000:
                        logger.info(f"Got Pexels image by {photographer}")
                        return ImageResult(
                            image_bytes=img_resp.content,
                            source=f"source: Pexels / {photographer}",
                        )
            except Exception:
                continue

        return None
    except Exception as e:
        logger.error(f"Pexels image search failed: {e}")
        return None


async def _bing_image_search(keywords: list[str]) -> Optional[ImageResult]:
    """
    Search Bing Images without an API key (scraping public results).
    Uses Bing's public image search with safe search enabled.
    """
    try:
        import random

        query = " ".join(keywords[:5])
        logger.info(f"Searching Bing Images for: {query}")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
        }

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(
                "https://www.bing.com/images/search",
                params={
                    "q": query,
                    "first": 1,
                    "count": 20,
                    "safesearch": "Strict",
                    "qft": "+filterui:imagesize-large",
                },
                headers=headers,
            )

        if resp.status_code != 200:
            return None

        # Extract image URLs from the HTML using murl pattern
        import re
        # Bing puts image URLs in murl:"..." attributes
        urls = re.findall(r'murl&quot;:&quot;(https?://[^&]+?)&quot;', resp.text)
        if not urls:
            # Alternate pattern
            urls = re.findall(r'murl":"(https?://[^"]+?)"', resp.text)

        if not urls:
            logger.warning("No Bing image URLs found")
            return None

        # Deduplicate and shuffle
        urls = list(dict.fromkeys(urls))  # Preserve order, remove dupes
        random.shuffle(urls)

        for img_url in urls[:8]:
            try:
                domain = urlparse(img_url).netloc.replace("www.", "")
                async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                    img_resp = await client.get(img_url)
                    if (img_resp.status_code == 200
                            and len(img_resp.content) > 2000
                            and img_resp.headers.get("content-type", "").startswith("image/")):
                        logger.info(f"Got Bing image from {domain}")
                        return ImageResult(
                            image_bytes=img_resp.content,
                            source=f"source: {domain}",
                        )
            except Exception:
                continue

        return None
    except Exception as e:
        logger.error(f"Bing image search failed: {e}")
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


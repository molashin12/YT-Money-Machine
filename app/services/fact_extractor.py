"""
Fact Extractor — uses Gemini to process raw content into structured facts
suitable for a social media card, plus YouTube video metadata.
"""

import json
import logging
from dataclasses import dataclass, field

from google.genai import types as genai_types

from app.services.api_key_manager import get_key_manager

logger = logging.getLogger(__name__)


@dataclass
class ExtractedFact:
    title: str  # Short headline (3-6 words)
    body: str  # The fact text (1-3 sentences)
    keywords: list[str]  # Keywords for image search
    yt_title: str = ""  # YouTube video title
    yt_description: str = ""  # YouTube short description
    yt_hashtags: list[str] = field(default_factory=list)  # YouTube hashtags


FACT_EXTRACTION_PROMPT = """You are a fact-extraction AI for a viral YouTube Shorts channel.

{channel_context}

Given the following raw content, extract the SINGLE most interesting, surprising, or educational fact from it.

Format your response as JSON with these exact keys:
{{
  "title": "Short catchy headline (3-6 words, no period)",
  "body": "The fact explained clearly in 1-3 short sentences. Keep it punchy and engaging for social media. Maximum 200 characters.",
  "keywords": ["keyword1", "keyword2", "keyword3"],
  "yt_title": "A catchy YouTube Shorts video title (max 70 chars, include an emoji)",
  "yt_description": "A brief YouTube description (2-3 sentences, engaging, include a call to action like 'Follow for more!')",
  "yt_hashtags": ["#hashtag1", "#hashtag2", "#hashtag3", "#hashtag4", "#hashtag5"]
}}

The keywords should describe what a relevant image would show — concrete, visual things (not abstract concepts).

Rules:
- The fact MUST be accurate and not misleading
- Keep language simple and accessible
- Make it feel like a viral tweet or social media post
- The body should be self-contained (understandable without the title)
- Keywords should be specific and visual (e.g., "honey jar" not "food preservation")
- yt_title should be attention-grabbing and suitable for YouTube Shorts
- yt_hashtags should include 5 relevant trending hashtags (with # prefix)
- Return ONLY the JSON, no other text

Raw content:
"""


async def extract_facts(raw_content: str, channel_description: str = "") -> ExtractedFact:
    """Extract structured facts + YouTube metadata from raw content using Gemini."""
    logger.info("Extracting facts with Gemini")

    # Build channel context if description is provided
    channel_context = ""
    if channel_description:
        channel_context = (
            f"CHANNEL STYLE GUIDE: {channel_description}\n"
            "Write the fact and YouTube metadata in the style and tone described above.\n"
        )

    try:
        manager = get_key_manager()

        prompt = FACT_EXTRACTION_PROMPT.format(channel_context=channel_context) + raw_content

        response = await manager.gemini_generate(
            model="gemini-2.5-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                temperature=0.7,
                response_mime_type="application/json",
            ),
        )

        # Parse JSON from response
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            text = text.rsplit("```", 1)[0]

        data = json.loads(text)

        return ExtractedFact(
            title=data.get("title", "Did You Know?"),
            body=data.get("body", raw_content[:200]),
            keywords=data.get("keywords", ["interesting", "facts"]),
            yt_title=data.get("yt_title", ""),
            yt_description=data.get("yt_description", ""),
            yt_hashtags=data.get("yt_hashtags", []),
        )

    except Exception as e:
        logger.error(f"Fact extraction failed: {e}")
        return ExtractedFact(
            title="Did You Know?",
            body=raw_content[:200] if raw_content else "An interesting fact",
            keywords=["interesting", "facts", "knowledge"],
        )

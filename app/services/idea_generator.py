"""
Idea Generator — uses Gemini to generate multiple unique video ideas
for a channel, avoiding duplicates from past videos.
"""

import json
import logging
from typing import Optional

from google.genai import types as genai_types

from app.services.api_key_manager import get_key_manager
from app.services.fact_extractor import ExtractedFact, _enforce_body_length
from app.services.video_history import get_past_titles

logger = logging.getLogger(__name__)

IDEA_GENERATION_PROMPT = """You create viral fact/story ideas for a YouTube Shorts channel.

{channel_context}

Generate EXACTLY {count} unique, interesting ideas. Each should be a self-contained fact or story.

AVOID these topics that were already covered:
{past_topics}

Return ONLY a JSON array:
[{{"title":"Short headline (3-6 words)","body":"The main fact — MUST be between 40 and 50 characters (including spaces). Count carefully.","keywords":["visual_kw1","visual_kw2","visual_kw3"],"yt_title":"YouTube Shorts title (max 70 chars, emoji)","yt_description":"Brief description (2-3 sentences)","yt_hashtags":["#tag1","#tag2","#tag3","#tag4","#tag5"]}}]

CRITICAL: Each "body" MUST be between 40 and 50 characters. Count every letter, space, and punctuation. Example of 45 chars: "Honey never spoils, even after 3000 years!!"

Rules:
- Every idea must be UNIQUE and different from the past topics
- Facts must be accurate
- Make them viral-worthy and engaging
- Keywords should be visual and concrete
- Return ONLY the JSON array"""


async def generate_ideas(
    channel_slug: str,
    count: int = 10,
    channel_description: str = "",
) -> list[ExtractedFact]:
    """Generate unique video ideas for a channel."""
    logger.info(f"Generating {count} ideas for channel: {channel_slug}")

    past = get_past_titles(channel_slug, limit=30)
    past_text = "\n".join(f"- {t}" for t in past) if past else "None yet"

    channel_context = ""
    if channel_description:
        channel_context = f"CHANNEL STYLE: {channel_description}"

    prompt = IDEA_GENERATION_PROMPT.format(
        channel_context=channel_context,
        count=count,
        past_topics=past_text,
    )

    try:
        manager = get_key_manager()
        response = await manager.gemini_generate(
            model="gemini-2.5-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                temperature=0.9,
                response_mime_type="application/json",
            ),
        )

        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            text = text.rsplit("```", 1)[0]

        ideas_data = json.loads(text)

        if not isinstance(ideas_data, list):
            ideas_data = [ideas_data]

        ideas = []
        for item in ideas_data[:count]:
            ideas.append(ExtractedFact(
                title=item.get("title", ""),
                body=_enforce_body_length(item.get("body", "")),
                keywords=item.get("keywords", []),
                yt_title=item.get("yt_title", ""),
                yt_description=item.get("yt_description", ""),
                yt_hashtags=item.get("yt_hashtags", []),
            ))

        logger.info(f"Generated {len(ideas)} ideas")
        return ideas

    except Exception as e:
        logger.error(f"Idea generation failed: {e}")
        return []

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
    body: str  # The fact text (5-8 sentences)
    keywords: list[str]  # Keywords for image search
    image_search_query: str = ""  # Best single search query for finding a photo
    yt_title: str = ""  # YouTube video title
    yt_description: str = ""  # YouTube short description
    yt_hashtags: list[str] = field(default_factory=list)  # YouTube hashtags


FACT_EXTRACTION_PROMPT = """You are a fact-extraction AI for a viral YouTube Shorts channel.

{channel_context}

Given the following raw content, extract the SINGLE most interesting, surprising, or educational fact from it.

Format your response as JSON with these exact keys:
{{
  "title": "Short catchy headline (3-6 words, no period)",
  "body": "The main fact text. MUST be between 25 and 35 words long (including spaces). Count carefully before answering.",
  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"],
  "image_search_query": "A single search query (3-5 words) to find a PHOTO directly related to this fact",
  "yt_title": "A catchy YouTube Shorts video title (max 70 chars, include an emoji)",
  "yt_description": "A brief YouTube description (2-3 sentences, engaging, include a call to action like 'Follow for more!')",
  "yt_hashtags": ["#hashtag1", "#hashtag2", "#hashtag3", "#hashtag4", "#hashtag5"]
}}

CRITICAL — BODY WORD COUNT RULES:
- The "body" field MUST be between 25 and 35 words (including spaces)
- Count every word carefully
- If the fact is too long, shorten it. If too short, add descriptive details
- Example of 45 chars: "Honey never spoils, even after 3000 years!!"
- NEVER produce body text shorter than 25 words or longer than 35 words

KEYWORD RULES (very important for finding the right image):
- If the fact is about a PERSON, the first keyword MUST be their full name
- If the fact is about an ANIMAL or OBJECT, include the exact species/item name
- If the fact is about a PLACE, include the place name
- Keywords should be Google Image Search-friendly (e.g., "Sam Ballard rugby player slug" not "dare")
- image_search_query should be the BEST single search to find a photo of the main subject (e.g., "Sam Ballard Australia" or "Iris Apfel fashion")

Rules:
- The fact MUST be accurate and not misleading
- Keep language simple and accessible
- Make it feel like a viral tweet or social media post
- The body should be self-contained (understandable without the title)
- Keywords should be specific and searchable — person names, place names, exact items (e.g., "Jerry Douthett Jack Russell terrier" not "dog bites toe")
- image_search_query is the single best phrase to Google Image Search for a photo of the subject
- yt_hashtags should include 5 relevant trending hashtags (with # prefix)
- Return ONLY the JSON, no other text

Raw content:
"""


def _enforce_body_length(body: str, min_words: int = 10, max_words: int = 35) -> str:
    """Ensure body text is within the target word count range."""
    body = body.strip()
    words = body.split()
    if len(words) <= max_words:
        return body
    # Truncate to max_words
    truncated = " ".join(words[:max_words])
    # Remove trailing punctuation that looks incomplete
    truncated = truncated.rstrip(",;: ")
    return truncated


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
            body=_enforce_body_length(data.get("body", raw_content[:200])),
            keywords=data.get("keywords", ["interesting", "facts"]),
            image_search_query=data.get("image_search_query", ""),
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

"""
Reddit Scraper API
Fetches top/hot posts from given subreddits to be used as video ideas.
Uses the official Reddit API via OAuth for reliability and cycle-able credentials.
"""

import aiohttp
import asyncio
import logging
import random
from typing import Optional

from app.services.fact_extractor import ExtractedFact
from app.services.reddit_history import is_post_seen, mark_post_seen

logger = logging.getLogger(__name__)

# List of common, realistic browser User-Agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]

def _get_random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def _format_fact_from_post(post: dict) -> ExtractedFact:
    """Format a Reddit post into an ExtractedFact ready for generation."""
    title = post.get("title", "")
    body = post.get("selftext", "")
    subreddit = post.get("subreddit", "")
    
    # If the post has no body, the title is the main fact.
    if not body.strip():
        body = title
    
    # Trim body to fit within constraints of a short video body if necessary
    # (Though we rely on video generator TTS not Gemini for exact word counts here, 
    # it's usually good to keep it relatively brief)
    words = body.split()
    if len(words) > 50:
        body = " ".join(words[:50]) + "..."
    if len(words) < 15 and body != title:
        body = f"{title}. {body}"

    # Generate visually descriptive mockup properties
    yt_title = f"{title[:60]}... 🤯" if len(title) > 60 else f"{title} 🤯"
    keywords = [subreddit.lower(), "reddit", "story"]
    
    return ExtractedFact(
        title=title[:50],  # Short title
        body=body.strip(),
        keywords=keywords,
        yt_title=yt_title,
        yt_description=f"Curated from r/{subreddit}. Enjoy this incredible story!\n\n#reddit #story #{subreddit.lower()}",
        yt_hashtags=[f"#{subreddit.lower()}", "#reddit", "#story", "#viral"]
    )


async def scrape_reddit_ideas(subreddits: list[str], count: int = 10) -> list[ExtractedFact]:
    """
    Scrape top/hot posts from the provided subreddits using standard JSON endpoints.
    Filters out duplicates using reddit_history.
    """
    if not subreddits:
        logger.warning("No subreddits provided for Reddit scraper.")
        return []

    # Rotate User-Agents to prevent instant HTTP 429 blocks
    headers = {
        "User-Agent": _get_random_user_agent(),
        "Accept": "application/json"
    }

    ideas = []
    # Mix subreddits or query them jointly (e.g., "r/AskReddit+todayilearned")
    subs_joined = "+".join(subreddits)
    # Fetch more than we need to account for duplicates and stickies
    fetch_limit = min(max(count * 3, 25), 100) 
    
    # Use public format instead of oauth.reddit.com
    url = f"https://www.reddit.com/r/{subs_joined}/hot.json?limit={fetch_limit}"
    
    logger.info(f"Scraping {fetch_limit} posts from r/{subs_joined} (No-API mode)...")

    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Use full browser headers to prevent HTTP 403/429 blocks
            headers = {
                "User-Agent": _get_random_user_agent(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    content_type = resp.headers.get("Content-Type", "")
                    
                    if resp.status == 200 and "application/json" in content_type:
                        data = await resp.json()
                        posts = data.get("data", {}).get("children", [])
                        
                        for child in posts:
                            post_data = child.get("data", {})
                            post_id = post_data.get("id")
                            
                            # Skip pinned stickies
                            if post_data.get("stickied"):
                                continue
                                
                            # Skip if already seen
                            if is_post_seen(post_id):
                                continue
                                
                            fact = _format_fact_from_post(post_data)
                            if fact:
                                ideas.append(fact)
                                mark_post_seen(post_id)
                                
                            if len(ideas) >= count:
                                break
                        
                        # If we successfully parsed JSON, exit retry loop
                        break
                    else:
                        text = await resp.text()
                        logger.warning(
                            f"Reddit API request failed (Attempt {attempt+1}/{max_retries}): "
                            f"Status {resp.status}, Content-Type: {content_type}"
                        )
                        if attempt < max_retries - 1:
                            delay = random.uniform(1.5, 3.5)
                            logger.info(f"Retrying in {delay:.1f} seconds...")
                            await asyncio.sleep(delay)
                        
        except Exception as e:
            logger.error(f"Error scraping Reddit on attempt {attempt+1}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
                
    logger.info(f"Reddit Scraper collected {len(ideas)} new ideas.")
    return ideas

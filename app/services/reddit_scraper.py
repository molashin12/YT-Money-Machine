"""
Reddit Scraper API
Fetches top/hot posts from given subreddits to be used as video ideas.
Uses the official Reddit API via OAuth for reliability and cycle-able credentials.
"""

import aiohttp
import asyncio
import base64
import logging
import random
from typing import Optional

from app.services.api_key_manager import get_key_manager
from app.services.fact_extractor import ExtractedFact
from app.services.reddit_history import is_post_seen, mark_post_seen

logger = logging.getLogger(__name__)

# Reddit API base URLs
REDDIT_AUTH_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_OAUTH_URL = "https://oauth.reddit.com"

# Headers mimicking a standard app to reduce blocking
USER_AGENT = "script:ai.youtubeshorts.generator:v1.0.0 (by /u/AutomatedIdeaGenerator)"


async def _get_access_token(client_id: str, client_secret: str) -> Optional[str]:
    """Get an OAuth token from Reddit using Client ID and Secret."""
    auth_str = f"{client_id}:{client_secret}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()
    
    headers = {
        "Authorization": f"Basic {b64_auth}",
        "User-Agent": USER_AGENT
    }
    data = {"grant_type": "client_credentials"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(REDDIT_AUTH_URL, headers=headers, data=data) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result.get("access_token")
                else:
                    text = await resp.text()
                    logger.error(f"Reddit auth failed ({resp.status}): {text}")
                    return None
    except Exception as e:
        logger.error(f"Error during Reddit auth: {e}")
        return None


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
    Scrape top/hot posts from the provided subreddits using official API.
    Filters out duplicates using reddit_history.
    """
    if not subreddits:
        logger.warning("No subreddits provided for Reddit scraper.")
        return []

    # Get credentials from API manager
    key_mgr = get_key_manager()
    reddit_creds = key_mgr.get_reddit_key()
    
    if not reddit_creds or ":" not in reddit_creds:
        logger.error("Reddit API credentials not configured properly. Format must be 'client_id:client_secret'.")
        return []
        
    client_id, client_secret = reddit_creds.split(":", 1)
    
    logger.info(f"Authenticating with Reddit API (Client ID: {client_id[:5]}...)")
    token = await _get_access_token(client_id, client_secret)
    
    if not token:
        logger.error("Failed to authenticate with Reddit API.")
        return []

    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT
    }

    ideas = []
    # Mix subreddits or query them jointly (e.g., "r/AskReddit+todayilearned")
    subs_joined = "+".join(subreddits)
    # Fetch more than we need to account for duplicates and stickies
    fetch_limit = min(max(count * 3, 25), 100) 
    
    url = f"{REDDIT_OAUTH_URL}/r/{subs_joined}/hot?limit={fetch_limit}"
    
    logger.info(f"Scraping {fetch_limit} posts from r/{subs_joined}...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    logger.error(f"Reddit API request failed ({resp.status}): {await resp.text()}")
                    return ideas
                
                data = await resp.json()
                posts = data.get("data", {}).get("children", [])
                
                for post_wrapper in posts:
                    post_data = post_wrapper.get("data", {})
                    post_id = post_data.get("name", "")  # e.g., 't3_12345'
                    
                    if not post_id or post_data.get("stickied", False):
                        continue
                        
                    if is_post_seen(post_id):
                        logger.debug(f"Skipping duplicate post: {post_id}")
                        continue
                        
                    # Valid fresh post
                    ideas.append(_format_fact_from_post(post_data))
                    mark_post_seen(post_id)
                    
                    if len(ideas) >= count:
                        break
                        
    except Exception as e:
        logger.error(f"Error fetching from Reddit API: {e}")
        
    logger.info(f"Reddit Scraper collected {len(ideas)} new ideas.")
    return ideas

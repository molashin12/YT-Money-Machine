"""
CSV Ideas Source Parser
Sequentially reads an uploaded CSV for a specific channel to generate ideas.
"""

import csv
import logging

from app import settings_store
from app.services.fact_extractor import ExtractedFact

logger = logging.getLogger(__name__)

async def scrape_csv_ideas(channel_slug: str, count: int = 1) -> list[ExtractedFact]:
    """Read un-used rows from the channel's custom CSV file."""
    ch = settings_store.get_channel(channel_slug)
    if not ch:
        logger.error(f"Channel not found: {channel_slug}")
        return []

    csv_path = settings_store.CHANNELS_DIR / channel_slug / "ideas.csv"
    if not csv_path.exists():
        logger.error(f"No custom CSV found for channel: {channel_slug}. Upload one in Settings.")
        return []

    last_index = ch.get("csv_last_row_index", 0)
    ideas = []
    
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            
            # Skip rows already processed
            for _ in range(last_index):
                try:
                    next(reader)
                except StopIteration:
                    logger.warning(f"Reached end of CSV for {channel_slug}.")
                    return ideas
            
            # Read the next `count` rows
            rows_read = 0
            while rows_read < count:
                try:
                    row = next(reader)
                except StopIteration:
                    break
                
                title = row.get("title", "").strip()
                body = row.get("body", "").strip()
                # post_id = row.get("id", "").strip() # unused right now unless we want history tracking
                
                if not body:
                    body = title
                
                # Truncate logic similar to reddit
                words = body.split()
                if len(words) > 50:
                    body = " ".join(words[:50]) + "..."
                if len(words) < 15 and body != title:
                    body = f"{title}. {body}"
                    
                yt_title = f"{title[:60]}... 🤯" if len(title) > 60 else f"{title} 🤯"
                
                fact = ExtractedFact(
                    title=title[:50],
                    body=body.strip(),
                    keywords=["story", "reddit"],
                    yt_title=yt_title,
                    yt_description=f"{title}\n\n#story #viral",
                    yt_hashtags=["#story", "#viral"]
                )
                ideas.append(fact)
                rows_read += 1
                
        if rows_read > 0:
            # Update last index in settings
            settings_store.update_channel(channel_slug, {"csv_last_row_index": last_index + rows_read})
            logger.info(f"Parsed {rows_read} rows from CSV. Next index is {last_index + rows_read}.")
            
    except Exception as e:
        logger.error(f"Error parsing CSV for {channel_slug}: {e}")

    return ideas

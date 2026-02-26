"""
Scheduler — APScheduler-based cron job runner for automated idea generation.

When a job fires:
1. Generates N fresh ideas for the channel
2. Sends ideas to the Telegram user for approval
3. User approves/rejects each idea inline
4. Approved ideas enter the video pipeline one by one
5. Finished videos + metadata are sent back via Telegram
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app import settings_store

logger = logging.getLogger(__name__)

scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    """Get the global scheduler instance."""
    global scheduler
    if scheduler is None:
        scheduler = AsyncIOScheduler()
    return scheduler


def start_scheduler():
    """Start the scheduler and load all enabled cron jobs."""
    sched = get_scheduler()
    if sched.running:
        return

    _sync_jobs(sched)
    sched.start()
    logger.info("Scheduler started")


def stop_scheduler():
    """Shutdown the scheduler."""
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    scheduler = None


def reload_jobs():
    """Reload jobs from settings (call after CRUD operations)."""
    sched = get_scheduler()
    _sync_jobs(sched)


def _sync_jobs(sched: AsyncIOScheduler):
    """Sync scheduler jobs with settings store."""
    # Remove all existing jobs
    existing_ids = {j.id for j in sched.get_jobs()}
    for job_id in existing_ids:
        if job_id.startswith("cron_"):
            sched.remove_job(job_id)

    # Add enabled jobs
    jobs = settings_store.list_cron_jobs()
    for job in jobs:
        if not job.get("enabled", False):
            continue

        job_id = f"cron_{job['id']}"
        schedule_time = job.get("schedule_time", "09:00")
        timezone = job.get("timezone", "Africa/Cairo")

        try:
            hour, minute = schedule_time.split(":")
            trigger = CronTrigger(
                hour=int(hour),
                minute=int(minute),
                timezone=timezone,
            )
            sched.add_job(
                _run_cron_job,
                trigger=trigger,
                id=job_id,
                args=[job],
                replace_existing=True,
            )
            logger.info(f"Scheduled job {job_id}: {job['channel_slug']} at {schedule_time} {timezone}")
        except Exception as e:
            logger.error(f"Failed to schedule job {job_id}: {e}")


async def _run_cron_job(job_config: dict):
    """Execute a cron job: generate ideas and send to Telegram."""
    from app.services.idea_generator import generate_ideas

    channel_slug = job_config["channel_slug"]
    num_ideas = job_config.get("num_ideas", 10)
    chat_id = job_config.get("telegram_chat_id")

    if not chat_id:
        logger.error(f"No chat_id for cron job {job_config['id']}")
        return

    # Get channel description
    channel_data = settings_store.get_channel(channel_slug)
    desc = channel_data.get("description", "") if channel_data else ""

    logger.info(f"Cron job firing: {channel_slug}, generating {num_ideas} ideas using {job_config.get('idea_source', 'ai')}")

    if job_config.get("idea_source") == "reddit":
        from app.services.reddit_scraper import scrape_reddit_ideas
        ideas = await scrape_reddit_ideas(
            subreddits=job_config.get("subreddits", []),
            count=num_ideas
        )
    else:
        ideas = await generate_ideas(
            channel_slug=channel_slug,
            count=num_ideas,
            channel_description=desc,
        )

    if not ideas:
        logger.error(f"No ideas generated for cron job {channel_slug}")
        return

    # Send ideas to Telegram for approval
    await _send_ideas_to_telegram(chat_id, job_config["id"], channel_slug, ideas)


async def _send_ideas_to_telegram(chat_id: int, job_id: str, channel_slug: str, ideas: list):
    """Send generated ideas to Telegram as inline keyboard messages."""
    from app.main import bot
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    if not bot:
        logger.error("Bot not initialized, can't send ideas")
        return

    # Store ideas temporarily for approval tracking
    _store_pending_ideas(job_id, channel_slug, ideas)

    # Header message
    channel_data = settings_store.get_channel(channel_slug)
    ch_name = channel_data["name"] if channel_data else channel_slug

    await bot.send_message(
        chat_id,
        f"💡 **{len(ideas)} New Ideas for {ch_name}**\n\n"
        "Tap ✅ to approve or ❌ to skip each idea.\n"
        "When done, tap **🚀 Generate Videos** to start.",
        parse_mode="Markdown",
    )

    # Send each idea as a message with approve/reject buttons
    for i, idea in enumerate(ideas):
        text = f"**{i+1}. {idea.title}**\n{idea.body}"
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Approve", callback_data=f"idea_approve:{job_id}:{i}"),
            InlineKeyboardButton(text="❌ Skip", callback_data=f"idea_skip:{job_id}:{i}"),
        ]])
        await bot.send_message(chat_id, text, reply_markup=kb, parse_mode="Markdown")
        await asyncio.sleep(0.3)  # Avoid rate limits

    # "Generate Videos" button
    gen_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Generate Videos from Approved Ideas", callback_data=f"idea_generate:{job_id}"),
    ]])
    await bot.send_message(chat_id, "👆 Approve/skip ideas above, then tap below:", reply_markup=gen_kb)


# ── Pending ideas storage (in-memory for the session) ──

_pending_ideas: dict = {}  # job_id -> {"channel_slug": str, "ideas": list, "approved": set}


def _store_pending_ideas(job_id: str, channel_slug: str, ideas: list):
    """Store ideas awaiting approval."""
    _pending_ideas[job_id] = {
        "channel_slug": channel_slug,
        "ideas": ideas,
        "approved": set(),
    }


def get_pending(job_id: str) -> Optional[dict]:
    """Get pending ideas for a job."""
    return _pending_ideas.get(job_id)


def approve_idea(job_id: str, index: int) -> bool:
    """Mark an idea as approved."""
    pending = _pending_ideas.get(job_id)
    if pending and 0 <= index < len(pending["ideas"]):
        pending["approved"].add(index)
        return True
    return False


def skip_idea(job_id: str, index: int) -> bool:
    """Mark an idea as skipped (remove from approved)."""
    pending = _pending_ideas.get(job_id)
    if pending:
        pending["approved"].discard(index)
        return True
    return False


def get_approved_ideas(job_id: str) -> list:
    """Get the list of approved ExtractedFact objects."""
    pending = _pending_ideas.get(job_id)
    if not pending:
        return []
    return [
        pending["ideas"][i]
        for i in sorted(pending["approved"])
        if i < len(pending["ideas"])
    ]


def clear_pending(job_id: str):
    """Clear pending ideas after generation."""
    _pending_ideas.pop(job_id, None)


# ── Pending video results (for per-video upload approval) ──

_pending_videos: dict = {}  # "vid_{unique_id}" -> {"channel_slug", "video_path", "result": VideoResult}
_video_counter = 0


def store_pending_video(channel_slug: str, result) -> str:
    """Store a generated video awaiting upload approval. Returns a unique video key."""
    global _video_counter
    _video_counter += 1
    vid_key = f"vid_{_video_counter}"
    _pending_videos[vid_key] = {
        "channel_slug": channel_slug,
        "video_path": result.video_path,
        "result": result,
    }
    return vid_key


def get_pending_video(vid_key: str) -> Optional[dict]:
    """Get a pending video by key."""
    return _pending_videos.get(vid_key)


def clear_pending_video(vid_key: str):
    """Remove a pending video after upload/skip."""
    _pending_videos.pop(vid_key, None)

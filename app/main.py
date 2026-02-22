"""
Main entry point — FastAPI application with Telegram bot integration.

Run with:
    uvicorn app.main:app --reload --port 8000
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.config import settings, OUTPUT_DIR
from app.bot.handlers import router as bot_router
from app.web.routes import router as web_router

# ── Logging ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Install in-memory log handler for admin dashboard
from app.log_handler import install_log_handler
install_log_handler()
logger = logging.getLogger(__name__)

# ── Bot setup ──────────────────────────────────────────────────────────
bot: Bot = None
dp: Dispatcher = None


def create_bot():
    global bot, dp
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not set — Telegram bot disabled")
        return
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(bot_router)


# ── Lifespan ───────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle events."""
    create_bot()

    if bot and settings.bot_mode == "webhook":
        webhook_url = f"{settings.base_url}/webhook/telegram"
        await bot.set_webhook(webhook_url)
        logger.info(f"Telegram webhook set: {webhook_url}")
    elif bot and settings.bot_mode == "polling":
        # Start polling in background
        logger.info("Starting Telegram bot in polling mode...")
        polling_task = asyncio.create_task(_start_polling())

    # Start cron scheduler
    from app.scheduler import start_scheduler, stop_scheduler
    start_scheduler()

    logger.info("🚀 YouTube Shorts Generator is running!")
    logger.info(f"   Web UI: http://localhost:8000")
    if bot:
        logger.info(f"   Bot mode: {settings.bot_mode}")

    yield

    # Shutdown
    stop_scheduler()
    if bot:
        if settings.bot_mode == "webhook":
            await bot.delete_webhook()
        await bot.session.close()
    logger.info("Shutdown complete.")


async def _start_polling():
    """Run the Telegram bot polling loop."""
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    except Exception as e:
        logger.error(f"Polling error: {e}")


# ── FastAPI app ────────────────────────────────────────────────────────
app = FastAPI(
    title="YouTube Shorts Generator",
    description="AI-powered YouTube Shorts automation",
    lifespan=lifespan,
)

# Web routes
app.include_router(web_router)

# Serve generated videos at /output/
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")


# Telegram webhook endpoint
@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Handle incoming Telegram updates via webhook."""
    if not bot or not dp:
        return {"ok": False, "error": "Bot not configured"}

    data = await request.json()
    update = Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot=bot, update=update)
    return {"ok": True}

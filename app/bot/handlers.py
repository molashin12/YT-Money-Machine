"""
Telegram Bot Handlers — manages the conversation flow:
1. User sends content (text, photo, or URL)
2. Bot asks which channel to use
3. User selects a channel
4. Bot processes and sends back the video

Management commands:
- /start — welcome message
- /channels — list all channels
- /addchannel — create a new channel
- /removechannel — remove a channel
- /settings — show settings summary
"""

import logging
from io import BytesIO

from aiogram import Router, Bot, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from app.bot.keyboards import channel_selection_keyboard, channel_delete_keyboard
from app.pipeline import generate_video
from app import settings_store
from app.services.api_key_manager import get_key_manager

logger = logging.getLogger(__name__)
router = Router()


class VideoGenStates(StatesGroup):
    waiting_for_content = State()
    waiting_for_channel = State()
    processing = State()


class ChannelCreationStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_template = State()
    waiting_for_sound_mode = State()
    waiting_for_duration = State()


class SettingsImportStates(StatesGroup):
    waiting_for_file = State()


# ══════════════════════════════════════════════════════════════════════
#  MANAGEMENT COMMANDS
# ══════════════════════════════════════════════════════════════════════


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Handle /start command."""
    await state.clear()
    chat_id = message.chat.id
    await message.answer(
        "👋 **Welcome to the YouTube Shorts Bot!**\n\n"
        f"🆔 **Your Chat ID:** `{chat_id}`\n"
        "_(Copy this to add yourself as a team member in the Admin Dashboard)_\n\n"
        "Send me any of the following:\n"
        "• 📝 Text with a fact or info\n"
        "• 🖼️ An image (with or without caption)\n"
        "• 🔗 A link (TikTok, IG Reel, YouTube Short)\n\n"
        "**Management commands:**\n"
        "/channels — List your channels\n"
        "/addchannel — Add a new channel\n"
        "/removechannel — Remove a channel\n"
        "/settings — View settings summary\n"
        "/export — Backup your settings to a JSON file\n"
        "/import — Restore settings from a JSON file",
        parse_mode="Markdown",
    )
    await state.set_state(VideoGenStates.waiting_for_content)


@router.message(Command("channels"))
async def cmd_channels(message: Message):
    """List all configured channels."""
    channels = settings_store.list_channels()
    if not channels:
        await message.answer("📺 No channels configured yet.\nUse /addchannel to create one.")
        return

    lines = ["📺 **Your Channels:**\n"]
    for ch in channels:
        template_status = "✅" if (settings_store.CHANNELS_DIR / ch["slug"] / "template.png").exists() else "⚠️ no template"
        sound = ch.get("sound_mode", "random")
        duration = ch.get("video_duration", 5)
        lines.append(
            f"• **{ch['name']}** (`{ch['slug']}`)\n"
            f"  🎵 {sound} · ⏱ {duration}s · {template_status}"
        )
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("addchannel"))
async def cmd_add_channel(message: Message, state: FSMContext):
    """Start channel creation flow."""
    await state.clear()
    await state.set_state(ChannelCreationStates.waiting_for_name)
    await message.answer(
        "📺 **Create a new channel**\n\n"
        "What's the channel name?",
        parse_mode="Markdown",
    )


@router.message(ChannelCreationStates.waiting_for_name)
async def handle_channel_name(message: Message, state: FSMContext):
    """Receive channel name."""
    name = message.text.strip()
    if not name:
        await message.answer("Please send a valid name.")
        return

    channel = settings_store.add_channel({"name": name})
    await state.update_data(new_channel_slug=channel["slug"])
    await state.set_state(ChannelCreationStates.waiting_for_template)
    await message.answer(
        f"✅ Channel **{name}** created (slug: `{channel['slug']}`)!\n\n"
        "Now send the **card template image** (PNG) for this channel.\n"
        "Or send /skip to do it later.",
        parse_mode="Markdown",
    )


@router.message(ChannelCreationStates.waiting_for_template, F.photo)
async def handle_channel_template(message: Message, state: FSMContext, bot: Bot):
    """Receive template image."""
    data = await state.get_data()
    slug = data["new_channel_slug"]

    photo = message.photo[-1]
    file = await bot.download(photo)
    image_bytes = file.read()
    settings_store.save_channel_template(slug, image_bytes)

    await state.set_state(ChannelCreationStates.waiting_for_sound_mode)
    await message.answer(
        "✅ Template saved!\n\n"
        "Choose the sound mode:\n"
        "• Send `random` — random music each time\n"
        "• Send `none` — no music\n"
        "• Send `specific` — choose a specific track",
    )


@router.message(ChannelCreationStates.waiting_for_template, F.text)
async def handle_channel_template_skip(message: Message, state: FSMContext):
    """Skip template upload."""
    if message.text.strip().lower() == "/skip":
        await state.set_state(ChannelCreationStates.waiting_for_sound_mode)
        await message.answer(
            "Skipped. Remember to upload a template later from the admin panel.\n\n"
            "Choose the sound mode:\n"
            "• Send `random`, `none`, or `specific`",
        )
    else:
        await message.answer("Please send a photo or /skip.")


@router.message(ChannelCreationStates.waiting_for_sound_mode)
async def handle_sound_mode(message: Message, state: FSMContext):
    """Receive sound mode."""
    mode = message.text.strip().lower()
    if mode not in ("random", "none", "specific"):
        await message.answer("Please send `random`, `none`, or `specific`.")
        return

    data = await state.get_data()
    slug = data["new_channel_slug"]
    settings_store.update_channel(slug, {"sound_mode": mode})

    await state.set_state(ChannelCreationStates.waiting_for_duration)
    await message.answer(
        "✅ Sound mode set!\n\n"
        "What should the video duration be? (in seconds, e.g. `5`)",
    )


@router.message(ChannelCreationStates.waiting_for_duration)
async def handle_duration(message: Message, state: FSMContext):
    """Receive video duration."""
    try:
        duration = int(message.text.strip())
        if duration < 3 or duration > 60:
            raise ValueError()
    except ValueError:
        await message.answer("Please send a number between 3 and 60.")
        return

    data = await state.get_data()
    slug = data["new_channel_slug"]
    settings_store.update_channel(slug, {"video_duration": duration})

    await state.clear()
    await state.set_state(VideoGenStates.waiting_for_content)
    await message.answer(
        f"🎉 **Channel setup complete!**\n\n"
        f"Duration set to {duration}s. You can now send content to create videos.\n"
        "Use /channels to see all channels.",
        parse_mode="Markdown",
    )


@router.message(Command("removechannel"))
async def cmd_remove_channel(message: Message):
    """Show channel removal keyboard."""
    channels = settings_store.list_channels()
    if not channels:
        await message.answer("No channels to remove.")
        return
    await message.answer(
        "🗑 Select a channel to remove:",
        reply_markup=channel_delete_keyboard(),
    )


@router.callback_query(F.data.startswith("delete_channel:"))
async def handle_delete_channel(callback: CallbackQuery):
    """Handle channel deletion."""
    slug = callback.data.split(":", 1)[1]
    channel = settings_store.get_channel(slug)
    name = channel["name"] if channel else slug

    if settings_store.delete_channel(slug):
        await callback.answer(f"Deleted {name}")
        await callback.message.edit_text(f"🗑 Channel **{name}** deleted.", parse_mode="Markdown")
    else:
        await callback.answer("Channel not found", show_alert=True)


@router.message(Command("settings"))
async def cmd_settings(message: Message):
    """Show settings summary."""
    manager = get_key_manager()
    channels = settings_store.list_channels()
    music = settings_store.list_music_files()
    data = settings_store.get_settings()
    api_keys = data.get("api_keys", {})

    gemini_count = len(api_keys.get("gemini", {}).get("keys", []))
    pexels_count = len(api_keys.get("pexels", {}).get("keys", []))
    cse_count = len(api_keys.get("google_cse", {}).get("keys", []))

    text = (
        "⚙️ **Settings Summary**\n\n"
        f"📺 **Channels:** {len(channels)}\n"
        f"🎵 **Music files:** {len(music)}\n\n"
        f"🔑 **API Keys:**\n"
        f"  • Gemini: {gemini_count} key(s) {'🔄' if api_keys.get('gemini', {}).get('cycling') else ''}\n"
        f"  • Pexels: {pexels_count} key(s) {'🔄' if api_keys.get('pexels', {}).get('cycling') else ''}\n"
        f"  • Google CSE: {cse_count} key(s) {'🔄' if api_keys.get('google_cse', {}).get('cycling') else ''}\n\n"
        "Manage keys and more at the web admin: `/admin`"
    )
    await message.answer(text, parse_mode="Markdown")


@router.message(Command("export"))
async def cmd_export(message: Message):
    """Export the settings.json file."""
    from aiogram.types import FSInputFile
    
    if not settings_store.SETTINGS_FILE.exists():
        await message.answer("❌ No settings file found yet.")
        return
        
    file = FSInputFile(path=settings_store.SETTINGS_FILE, filename="settings.json")
    await message.answer_document(
        document=file,
        caption="📥 **Your Configuration Backup**\n\nKeep this file safe! It contains all your API keys, channels, and schedules.",
        parse_mode="Markdown"
    )


@router.message(Command("import"))
async def cmd_import(message: Message, state: FSMContext):
    """Start the settings import flow."""
    await state.clear()
    await state.set_state(SettingsImportStates.waiting_for_file)
    await message.answer(
        "📤 **Import Settings**\n\n"
        "Please reply with your `settings.json` file.\n\n"
        "⚠️ **WARNING:** This will OVERWRITE your current settings, keys, and channels completely. "
        "Send /cancel to abort.",
        parse_mode="Markdown"
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    """Cancel the current operation."""
    await state.clear()
    await state.set_state(VideoGenStates.waiting_for_content)
    await message.answer("Operation cancelled.")


@router.message(SettingsImportStates.waiting_for_file, F.document)
async def handle_settings_import(message: Message, state: FSMContext, bot: Bot):
    """Handle the uploaded settings.json file."""
    import json
    
    document = message.document
    if not document.file_name.endswith('.json'):
        await message.answer("❌ Please send a valid `.json` file.")
        return
        
    try:
        # Download file
        file = await bot.get_file(document.file_id)
        file_bytes = await bot.download_file(file.file_path)
        content = file_bytes.read().decode('utf-8')
        
        # Parse JSON and save
        data = json.loads(content)
        settings_store.save_settings(data)
        
        # Reload services
        import app.services.api_key_manager as key_mgr
        key_mgr.reload_key_manager()
        from app.scheduler import reload_jobs
        reload_jobs()
        
        await state.clear()
        await state.set_state(VideoGenStates.waiting_for_content)
        await message.answer(
            "✅ **Settings Imported Successfully!**\n\n"
            "All keys, channels, and cron jobs have been updated. Services have been reloaded.",
            parse_mode="Markdown"
        )
    except json.JSONDecodeError:
        await message.answer("❌ The file contains invalid JSON data.")
    except Exception as e:
        logger.error(f"Failed to import settings via bot: {e}")
        await message.answer(f"❌ Failed to import: {str(e)[:100]}")


# ══════════════════════════════════════════════════════════════════════
#  VIDEO GENERATION FLOW
# ══════════════════════════════════════════════════════════════════════


@router.message(VideoGenStates.processing)
async def handle_busy(message: Message):
    """User sends a message while bot is processing."""
    await message.answer("⏳ Still working on your video. Please wait...")


@router.message(F.photo)
async def handle_photo(message: Message, state: FSMContext, bot: Bot):
    """Handle photo messages."""
    photo = message.photo[-1]
    file = await bot.download(photo)
    image_bytes = file.read()
    caption = message.caption or ""

    await state.update_data(input_text=caption, input_image=image_bytes)
    await state.set_state(VideoGenStates.waiting_for_channel)
    await message.answer(
        "📸 Got your image! Choose the channel:",
        reply_markup=channel_selection_keyboard(),
    )


@router.message(F.text)
async def handle_text(message: Message, state: FSMContext):
    """Handle text messages (URL or plain text)."""
    text = message.text.strip()
    await state.update_data(input_text=text, input_image=None)
    await state.set_state(VideoGenStates.waiting_for_channel)
    await message.answer(
        "📝 Got your content! Choose the channel:",
        reply_markup=channel_selection_keyboard(),
    )


@router.callback_query(F.data.startswith("channel:"))
async def handle_channel_selection(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Handle channel selection from inline keyboard."""
    channel_slug = callback.data.split(":", 1)[1]
    await callback.answer("Processing...")

    data = await state.get_data()
    input_text = data.get("input_text", "")
    input_image = data.get("input_image")

    if not input_text and not input_image:
        await callback.message.answer("❌ No content found. Send me text, an image, or a link first.")
        await state.set_state(VideoGenStates.waiting_for_content)
        return

    await state.set_state(VideoGenStates.processing)
    progress_msg = await callback.message.answer("🚀 Starting video generation...\nThis may take a minute.")

    async def progress_callback(step: str):
        try:
            await progress_msg.edit_text(f"🚀 **Video Generation**\n\n{step}")
        except Exception:
            pass

    async def _run_generation():
        result = await generate_video(
            channel_slug=channel_slug,
            text=input_text,
            image_bytes=input_image,
            progress_callback=progress_callback,
        )

        if result:
            try:
                from app.scheduler import store_pending_video
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

                with open(result.video_path, "rb") as vf:
                    video_data = vf.read()
                video_file = BufferedInputFile(file=video_data, filename="youtube_short.mp4")
                await callback.message.answer_video(video=video_file, caption="✅ Your YouTube Short is ready!")

                # Send metadata + Upload/Skip buttons
                vid_key = store_pending_video(channel_slug, result)
                yt_info = "📋 **YouTube Metadata:**\n\n"
                if result.yt_title:
                    yt_info += f"**Title:** {result.yt_title}\n\n"
                if result.yt_description:
                    yt_info += f"**Description:**\n{result.yt_description}\n\n"
                if result.yt_hashtags:
                    yt_info += f"**Hashtags:**\n{' '.join(result.yt_hashtags)}"

                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="📤 Upload to YouTube", callback_data=f"yt_upload:{vid_key}"),
                    InlineKeyboardButton(text="❌ Skip Upload", callback_data=f"yt_skip:{vid_key}"),
                ]])
                await callback.message.answer(yt_info, parse_mode="Markdown", reply_markup=kb)
            except Exception as e:
                logger.error(f"Failed to send video: {e}")
                await callback.message.answer(f"✅ Video done but couldn't send: {e}")
        else:
            await callback.message.answer("❌ Video generation failed. Check logs.")

        await state.clear()
        await state.set_state(VideoGenStates.waiting_for_content)

    import asyncio
    asyncio.create_task(_run_generation())


# ══════════════════════════════════════════════════════════════════════
#  YOUTUBE UPLOAD APPROVAL (per-video)
# ══════════════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("yt_upload:"))
async def handle_yt_upload(callback: CallbackQuery):
    """Upload a video to YouTube as a private draft."""
    from app.scheduler import get_pending_video, clear_pending_video
    from app.services.youtube_uploader import upload_to_youtube, is_channel_connected

    vid_key = callback.data.split(":")[1]
    pending = get_pending_video(vid_key)
    if not pending:
        await callback.answer("Video no longer available", show_alert=True)
        return

    channel_slug = pending["channel_slug"]
    result = pending["result"]

    if not is_channel_connected(channel_slug):
        await callback.answer(
            "⚠️ YouTube not connected for this channel! Go to Admin → Channel → Connect YouTube.",
            show_alert=True,
        )
        return

    await callback.answer("📤 Uploading to YouTube...")
    await callback.message.edit_reply_markup(reply_markup=None)
    status_msg = await callback.message.answer("⏳ Uploading to YouTube as draft...")

    try:
        upload_result = await upload_to_youtube(
            channel_slug=channel_slug,
            video_path=result.video_path,
            title=result.yt_title,
            description=result.yt_description,
            tags=result.yt_hashtags,
        )

        if upload_result:
            await status_msg.edit_text(
                f"✅ **Uploaded to YouTube (Private/Draft)**\n\n"
                f"🔗 {upload_result['url']}\n\n"
                f"Go to YouTube Studio to publish it.",
                parse_mode="Markdown",
            )
        else:
            await status_msg.edit_text("❌ YouTube upload failed. Check your OAuth connection.")
    except Exception as e:
        logger.error(f"YouTube upload failed: {e}")
        await status_msg.edit_text(f"❌ Upload error: {str(e)[:200]}")

    clear_pending_video(vid_key)


@router.callback_query(F.data.startswith("yt_skip:"))
async def handle_yt_skip(callback: CallbackQuery):
    """Skip YouTube upload for a video."""
    from app.scheduler import clear_pending_video

    vid_key = callback.data.split(":")[1]
    clear_pending_video(vid_key)
    await callback.answer("Skipped")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
        text = callback.message.text or ""
        await callback.message.edit_text(f"⏭️ Upload skipped\n\n{text}", parse_mode="Markdown")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
#  CRON JOB IDEA APPROVAL FLOW
# ══════════════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("idea_approve:"))
async def handle_idea_approve(callback: CallbackQuery):
    """Approve a generated idea."""
    from app.scheduler import approve_idea
    parts = callback.data.split(":")
    job_id = parts[1]
    index = int(parts[2])
    approve_idea(job_id, index)
    await callback.answer("✅ Approved!")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
        text = callback.message.text or ""
        await callback.message.edit_text(f"✅ {text}")
    except Exception:
        pass


@router.callback_query(F.data.startswith("idea_skip:"))
async def handle_idea_skip(callback: CallbackQuery):
    """Skip a generated idea and automatically fetch a replacement."""
    from app.scheduler import skip_idea, fetch_single_replacement_idea
    parts = callback.data.split(":")
    job_id = parts[1]
    index = int(parts[2])
    skip_idea(job_id, index)
    
    await callback.answer("❌ Skipped")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
        text = callback.message.text or ""
        await callback.message.edit_text(f"❌ ~~{text}~~\n_Fetching replacement..._", parse_mode="Markdown")
    except Exception:
        pass

    # Fetch and send a replacement idea
    replacement_data = await fetch_single_replacement_idea(job_id)
    if replacement_data:
        new_idea, new_index = replacement_data
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        new_text = f"**{new_index+1}. {new_idea.title}**"
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Approve", callback_data=f"idea_approve:{job_id}:{new_index}"),
            InlineKeyboardButton(text="❌ Skip", callback_data=f"idea_skip:{job_id}:{new_index}"),
        ]])
        await callback.message.answer(new_text, reply_markup=kb, parse_mode="Markdown")
    else:
        await callback.message.answer("⚠️ Could not fetch a replacement idea.")


@router.callback_query(F.data.startswith("idea_generate:"))
async def handle_idea_generate(callback: CallbackQuery):
    """Generate videos from all approved ideas, each with per-video upload approval."""
    from app.scheduler import get_approved_ideas, get_pending, clear_pending, store_pending_video

    job_id = callback.data.split(":")[1]
    pending = get_pending(job_id)
    if not pending:
        await callback.answer("No pending ideas found", show_alert=True)
        return

    approved = get_approved_ideas(job_id)
    if not approved:
        await callback.answer("No ideas approved! Approve some first.", show_alert=True)
        return

    channel_slug = pending["channel_slug"]
    await callback.answer(f"Starting {len(approved)} videos...")
    await callback.message.edit_text(
        f"🚀 **Generating {len(approved)} videos...**\nEach one will be sent for your approval.",
        parse_mode="Markdown",
    )

    async def _run_batch_generation():
        for i, idea in enumerate(approved):
            try:
                status_msg = await callback.message.answer(
                    f"⏳ Video {i+1}/{len(approved)}: *{idea.title}*...",
                    parse_mode="Markdown",
                )

                result = await generate_video(
                    channel_slug=channel_slug,
                    text=f"{idea.title}\n\n{idea.body}",
                    progress_callback=None,
                )

                # Delete progress message
                try:
                    await status_msg.delete()
                except Exception:
                    pass

                if result:
                    # Send video
                    with open(result.video_path, "rb") as vf:
                        video_data = vf.read()
                    video_file = BufferedInputFile(file=video_data, filename=f"short_{i+1}.mp4")
                    await callback.message.answer_video(
                        video=video_file,
                        caption=f"✅ Video {i+1}/{len(approved)}: {idea.title}",
                    )

                    # Send metadata + Upload/Skip buttons
                    vid_key = store_pending_video(channel_slug, result)
                    meta = f"📋 **Video {i+1} Metadata:**\n"
                    if result.yt_title:
                        meta += f"**Title:** {result.yt_title}\n"
                    if result.yt_description:
                        meta += f"**Description:** {result.yt_description}\n"
                    if result.yt_hashtags:
                        meta += f"**Hashtags:** {' '.join(result.yt_hashtags)}"

                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                    kb = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(
                            text="📤 Upload to YouTube",
                            callback_data=f"yt_upload:{vid_key}",
                        ),
                        InlineKeyboardButton(
                            text="❌ Skip Upload",
                            callback_data=f"yt_skip:{vid_key}",
                        ),
                    ]])
                    await callback.message.answer(meta, parse_mode="Markdown", reply_markup=kb)
                else:
                    await callback.message.answer(f"❌ Video {i+1} failed: {idea.title}")

            except Exception as e:
                logger.error(f"Failed to generate video {i+1}: {e}")
                await callback.message.answer(f"❌ Video {i+1} error: {str(e)[:100]}")

        clear_pending(job_id)
        await callback.message.answer(
            f"🎉 **Done!** Generated {len(approved)} videos.\n"
            "Tap 📤 on each video above to upload to YouTube as draft.",
            parse_mode="Markdown",
        )

    import asyncio
    asyncio.create_task(_run_batch_generation())

"""
Telegram bot inline keyboards for channel selection and management.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app import settings_store


def channel_selection_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard with buttons for each channel (video generation)."""
    channels = settings_store.list_channels()
    buttons = []
    for ch in channels:
        buttons.append([
            InlineKeyboardButton(
                text=f"📺 {ch['name']}",
                callback_data=f"channel:{ch['slug']}",
            )
        ])
    if not buttons:
        buttons.append([
            InlineKeyboardButton(text="⚠️ No channels — use /addchannel", callback_data="noop")
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def channel_delete_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for channel deletion."""
    channels = settings_store.list_channels()
    buttons = []
    for ch in channels:
        buttons.append([
            InlineKeyboardButton(
                text=f"🗑 {ch['name']}",
                callback_data=f"delete_channel:{ch['slug']}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

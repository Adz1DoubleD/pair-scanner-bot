"""Handles the replies the scanner asks the owner to type."""

from telegram import Update
from telegram.ext import ContextTypes

from .blocks import settings
from .settings import is_owner


async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a typed reply the scanner asked for, such as a new setting value."""
    if not is_owner(update.effective_user.id):
        return
    state = context.user_data.get("state") or ""
    if not state.startswith("scanner:settings:"):
        return
    text = (update.message.text or "").strip()
    try:
        await update.message.delete()
    except Exception:
        pass
    await settings.handle_text(update, context, text)

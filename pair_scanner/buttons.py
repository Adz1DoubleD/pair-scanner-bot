"""Routes the scanner's button presses to the right screen."""

import csv
import io
from datetime import datetime
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from .blocks import dashboard, settings
from .blocks.balances import balances_block
from .db.pairs import pairs_get_by_status
from .settings import MODE_NAMES, current_mode, forced_mode, is_owner


async def menu_block() -> dict[str, Any]:
    forced = forced_mode()
    text = (
        "🎯 *Pair Scanner*\n\n"
        f"🤖 Pairs: {MODE_NAMES[current_mode('pairs')]}\n"
        f"🧭 Strategy: {MODE_NAMES[current_mode('strategy')]}"
        + (
            f"\n_Capped at {MODE_NAMES[forced]} for this run_"
            if forced is not None
            else ""
        )
    )
    return {
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🎯 Scanner", callback_data="scanner:dashboard"
                    ),
                    InlineKeyboardButton(
                        "₿ Balances", callback_data="scanner:balances"
                    ),
                ],
                [InlineKeyboardButton("⚙️ Settings", callback_data="scanner:settings")],
                [InlineKeyboardButton("❌ Close", callback_data="cancel")],
            ]
        ),
    }


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a scanner button press. A host routes "scanner:" callbacks here."""
    query = update.callback_query
    data = query.data

    if not is_owner(query.from_user.id):
        await query.answer("This bot only answers its owner.", show_alert=True)
        return

    if data.startswith("scanner:settings"):
        await settings.handle_button(update, context)
        return

    await query.answer()

    if data == "ignore":
        return

    if data == "cancel":
        try:
            await query.message.delete()
        except BadRequest:
            await query.edit_message_reply_markup(None)
        return

    if data.startswith("scanner:export:"):
        table = data.split(":")[2]
        if table not in ("pairs", "positions"):
            return
        rows = await pairs_get_by_status(None, table) or []
        buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        stamp = datetime.now().strftime("%Y-%m-%d")
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=io.BytesIO(buf.getvalue().encode("utf-8-sig")),
            filename=f"{table}_{stamp}.csv",
            caption=f"📤 {table.capitalize()}: {len(rows)} rows",
        )
        return

    if data in (
        "scanner:menu",
        "scanner:dashboard",
        "scanner:home",
        "scanner:balances",
    ):
        if data == "scanner:menu":
            block = await menu_block()
        elif data in ("scanner:dashboard", "scanner:home"):
            block = await dashboard.handle_block(context)
        else:
            block = await balances_block()
        await query.edit_message_text(
            block["text"],
            reply_markup=block.get("reply_markup"),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return

    await dashboard.handle_button(update, context)

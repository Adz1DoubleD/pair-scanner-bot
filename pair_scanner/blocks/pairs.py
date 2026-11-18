from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import BOT_SETTINGS, TIMEZONE
from config.scanner import PAIR_STATUS
from config.w3 import NETWORKS
from services.db import get_neighbours
from services.db.pairs import (
    pairs_get_by_id,
    pairs_get_by_status,
    pairs_get_rejection_counts,
)
from services.w3.audit import (
    audit_pair,
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ...utils import (
    escape_markdown,
    format_duration,
    format_timestamp,
)


async def block_detail(context: ContextTypes.DEFAULT_TYPE, row_id, table):
    pair_data = await pairs_get_by_id(row_id, "pairs")
    prev_id, next_id = await get_neighbours(int(row_id), "pairs")

    if not pair_data:
        return {
            "text": "❌ Pair not found (it may have been cleared).",
            "reply_markup": InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back", callback_data="back:scanner")]]
            ),
        }

    symbol = escape_markdown(pair_data.get("symbol") or "Unknown")
    target = pair_data["target_address"]
    pair_addr = pair_data["pair_address"]
    status = pair_data["status"].upper()

    local_tz = ZoneInfo(TIMEZONE)
    now = datetime.now(local_tz)
    found_at = pair_data["found_at"].astimezone(local_tz)
    audit_time = float(BOT_SETTINGS.get("audit_cooldown", 120))
    unlock_time = found_at + timedelta(minutes=audit_time)

    remaining_str = ""
    if not pair_data["audit_done"]:
        if now < unlock_time:
            diff = unlock_time - now
            total_mins = int(diff.total_seconds() / 60)
            remaining_str = f" in {format_duration(total_mins)}"
        else:
            remaining_str = " Ready!"

    date_str, age_str = format_timestamp(pair_data["found_at"])
    network = pair_data["network"]
    network_config = NETWORKS.get(network, NETWORKS["Base"])
    explorer = network_config["scan"].replace("/tx/", "/address/")
    dex = network_config["dex"]

    retry_count = pair_data.get("retry_count", 0)
    retry_str = f" ({retry_count} retries)" if retry_count > 0 else ""

    msg = (
        f"💎 *Pair Details: #{row_id}* (${symbol})\n\n"
        f"🚦 *Status:* `{status}` {retry_str}\n"
        f"🌐 *Network:* {network}\n"
        f"🎯 *Token:* `{target}`\n"
        f"💧 *Pair:* `{pair_addr}`\n"
        f"🕒 *Found:* {date_str}\n"
        f"⏳ *Age:* {age_str}\n"
        f"📈 *Audit:*{'✅ Done' if pair_data['audit_done'] else remaining_str}\n"
    )

    keyboard = [
        [
            InlineKeyboardButton("🌍 Token Explorer", url=f"{explorer}{target}"),
            InlineKeyboardButton("📊 Chart", url=f"{dex}{pair_addr}"),
        ]
    ]

    if status != "completed":
        keyboard.append(
            [
                InlineKeyboardButton(
                    "👀 Preview", callback_data=f"scanner:preview:{row_id}"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "🗑️ Remove Pair",
                callback_data=f"scanner:remove_confirm:{table}:{row_id}",
            )
        ]
    )

    nav_row = []
    if prev_id:
        nav_row.append(
            InlineKeyboardButton(
                f"⬅️ {prev_id}", callback_data=f"scanner:status:{prev_id}"
            )
        )
    else:
        nav_row.append(InlineKeyboardButton(" ", callback_data="ignore"))

    nav_row.append(
        InlineKeyboardButton("🔄 Refresh", callback_data=f"scanner:status:{row_id}")
    )

    if next_id:
        nav_row.append(
            InlineKeyboardButton(
                f"{next_id} ➡️", callback_data=f"scanner:status:{next_id}"
            )
        )
    else:
        nav_row.append(InlineKeyboardButton(" ", callback_data="ignore"))

    keyboard.append(nav_row)
    keyboard.append(
        [
            InlineKeyboardButton(
                f"🔙 Back to {pair_data['status'].replace('-', ' ').title()}",
                callback_data=f"scanner:status:{pair_data['status']}",
            )
        ]
    )
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="cancel")])

    return {
        "text": msg,
        "reply_markup": InlineKeyboardMarkup(keyboard),
        "disable_web_page_preview": True,
    }


async def block_list(context: ContextTypes.DEFAULT_TYPE, status_arg):
    parts = status_arg.split(":")
    status_input = parts[0].lower()
    current_page = int(parts[1]) if len(parts) > 1 else 0

    found_pairs = await pairs_get_by_status(status_input, "pairs")
    if not found_pairs:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Back ", callback_data="back:scanner")]]
        )
        return {
            "text": f"No pairs found with status: {status_input.capitalize()}",
            "reply_markup": kb,
        }

    page_size = 10
    total_count = len(found_pairs)
    total_pages = (total_count + page_size - 1) // page_size
    chunk = found_pairs[current_page * page_size : (current_page + 1) * page_size]

    details = []
    id_buttons = []
    for entry in chunk:
        rid = entry["id"]
        sym = escape_markdown(entry.get("symbol") or "???")
        _, age_str = format_timestamp(entry.get("found_at"))
        details.append(f"`{rid}` | *{sym}* | {age_str}")
        id_buttons.append(
            InlineKeyboardButton(f"{rid}", callback_data=f"scanner:status:{rid}")
        )

    response_text = (
        f"📋 *{status_input.upper()} Pairs* (Page {current_page + 1}/{total_pages})\n\n"
        + "\n".join(details)
    )

    keyboard = []
    nav_row = []
    if current_page > 0:
        nav_row.append(
            InlineKeyboardButton(
                "⬅️ Prev",
                callback_data=f"scanner:status:{status_input}:{current_page - 1}",
            )
        )
    if current_page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(
                "Next ➡️",
                callback_data=f"scanner:status:{status_input}:{current_page + 1}",
            )
        )
    if nav_row:
        keyboard.append(nav_row)
    for i in range(0, len(id_buttons), 5):
        keyboard.append(id_buttons[i : i + 5])
    keyboard.append(
        [InlineKeyboardButton("🔙 Back to Scanner", callback_data="back:scanner")]
    )

    return {
        "text": response_text,
        "reply_markup": InlineKeyboardMarkup(keyboard),
        "disable_web_page_preview": True,
    }


async def button_buy_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    row_id = parts[2]
    record = await pairs_get_by_id(row_id, "pairs")
    symbol = escape_markdown((record or {}).get("symbol") or "Unknown")

    await query.edit_message_text(
        f"⚠️ *Confirm manual buy?*\n\n${symbol} #{row_id}\n",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data=f"scanner:status:{row_id}",
                    ),
                    InlineKeyboardButton(
                        "✅ Yes, Buy",
                        callback_data=f"scanner:buy:{row_id}",
                    ),
                ]
            ]
        ),
    )


async def button_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    row_id = parts[2]

    pair_data = await pairs_get_by_id(row_id, "pairs")
    result = await audit_pair(context, pair_data, force_buy=True) if pair_data else ""

    block = await block_detail(context, row_id, "pairs")
    await query.edit_message_text(
        f"{block['text']}\n\n*{result}*" if result else block["text"],
        reply_markup=block["reply_markup"],
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


async def button_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    row_id = parts[2]

    pair_data = await pairs_get_by_id(row_id, "pairs")
    block = await block_detail(context, row_id, "pairs")
    if not pair_data:
        await query.edit_message_text(
            block["text"], reply_markup=block["reply_markup"], parse_mode="Markdown"
        )
        return

    preview = await audit_pair(context, pair_data, preview=True)

    preview_cb = f"scanner:preview:{row_id}"
    buy_btn = InlineKeyboardButton(
        "💰 Buy", callback_data=f"scanner:buy_confirm:{row_id}"
    )
    rows = [
        [buy_btn if b.callback_data == preview_cb else b for b in row]
        for row in block["reply_markup"].inline_keyboard
    ]

    await query.edit_message_text(
        f"{block['text']}\n{preview}",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


async def button_rejected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    counts = await pairs_get_rejection_counts()
    kb = []
    for s in PAIR_STATUS:
        count = counts.get(s, 0)
        if count > 0:
            display = s.replace("-", " ").title()
            kb.append(
                [
                    InlineKeyboardButton(
                        f"📁 {display} ({count})",
                        callback_data=f"scanner:status:{s}",
                    )
                ]
            )

    kb.append(
        [InlineKeyboardButton("🔙 Back to Scanner", callback_data="back:scanner")]
    )
    kb.append([InlineKeyboardButton("❌ Close", callback_data="cancel")])

    await query.edit_message_text(
        text="❌ *Rejected Audit Breakdown*\n\nSelect a category to view pairs:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown",
    )

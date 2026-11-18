from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .. import jobs
from ..constants import PAIR_STATUS, STATS_PERIODS
from ..db.pairs import (
    pairs_get_by_id,
    pairs_get_stats,
    pairs_mark_completed,
)
from ..helpers import escape_markdown, format_timestamp
from ..monitors import run_sell_all, run_strategy_monitor
from ..w3.tools import fetch_eth_usd_price
from . import pairs, positions


async def handle_block(
    context: ContextTypes.DEFAULT_TYPE, status_arg=None, table="pairs", brief=False
):
    module = positions if table == "positions" else pairs
    if status_arg and status_arg.isdigit():
        return await module.block_detail(context, status_arg, table)
    if status_arg:
        return await module.block_list(context, status_arg)
    return await _block(context, brief=brief)


async def _block(context: ContextTypes.DEFAULT_TYPE, days=1, brief=False):
    stats, last_buy, last_sell, last_scan = await pairs_get_stats(days=days)
    mon_count = stats.get("active_positions", 0)

    if not stats or stats.get("total", 0) == 0:
        if brief:
            return {"text": ""}
        return {
            "text": "🎯 *Pairs Dashboard*\n\n📡 No pairs found yet.",
            "reply_markup": InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🔄 Refresh", callback_data="scanner:home")],
                    [InlineKeyboardButton("❌ Close", callback_data="cancel")],
                ]
            ),
        }

    _, last_scan_ts = (
        format_timestamp(last_scan["found_at"]) if last_scan else (None, "Never")
    )

    _, last_buy_ts = (
        format_timestamp(last_buy["found_at"]) if last_buy else (None, "Never")
    )
    _, last_sell_ts = (
        format_timestamp(last_sell["found_at"]) if last_sell else (None, "Never")
    )

    title = (
        "🤖 Token Scanner"
        if brief
        else f"🤖 Token Scanner - {STATS_PERIODS[days]} Stats"
    )
    stat_text = (
        f"*{title}*\n"
        f" ├ Found: {stats.get('pulse_total', 0)}\n"
        f" ├ Rejected: {stats.get('pulse_rejected', 0)}\n"
        f" ├ Bought: {stats.get('pulse_passed', 0)}\n"
    )

    closed = int(stats.get("pulse_closed", 0) or 0)
    if closed:
        avg_pnl = float(stats.get("pulse_avg_pnl") or 0)
        wins = int(stats.get("pulse_wins", 0) or 0)
        win_rate = wins / closed * 100
        eth_pnl = float(stats.get("pulse_eth_pnl") or 0)
        eth_counted = int(stats.get("pulse_eth_counted") or 0)
        eth_line = ""
        if eth_counted:
            usd = eth_pnl * await fetch_eth_usd_price()
            usd_str = f" ({'-' if usd < 0 else '+'}${abs(usd):.2f})" if usd else ""
            eth_line = f" ├ P&L: `{eth_pnl:+.6f} ETH`{usd_str}\n"
        stat_text += (
            f" ├ Sold: {closed}\n"
            f"{eth_line}"
            f" ├ Avg Return: `{avg_pnl:+.2f}%`\n"
            f" └ Win Rate: `{win_rate:.0f}%` ({wins}/{closed})\n\n"
        )
    else:
        stat_text += " └ Sold: 0\n\n"

    if brief:
        return {"text": stat_text}

    next_pairs, next_pairs_delta = jobs.job_status(jobs.application, "Pairs Monitor")
    next_strategy, next_strategy_delta = jobs.job_status(
        jobs.application, "Strategy Monitor"
    )
    activity = [
        f"Monitoring: {mon_count}",
        f"Last Buy: {last_buy_ts}",
        f"Last Sell: {last_sell_ts}",
        f"Last Scan: {last_scan_ts}",
    ]
    schedule = [
        f"Pairs Monitor: {next_pairs} ({next_pairs_delta})",
        f"Strategy Monitor: {next_strategy} ({next_strategy_delta})",
    ]
    if mon_count > 0:
        next_sell, next_sell_delta = jobs.job_status(jobs.application, "Sell Monitor")
        schedule.append(f"Sell Monitor: {next_sell} ({next_sell_delta})")

    for title, lines in (("🕒 *Activity*", activity), ("⏭️ *Next Run*", schedule)):
        stat_text += (
            f"{title}\n"
            + "\n".join(
                f" {'└' if i == len(lines) - 1 else '├'} {line}"
                for i, line in enumerate(lines)
            )
            + "\n\n"
        )

    stat_text = stat_text.rstrip()

    keyboard = [
        [
            InlineKeyboardButton(
                f"• {label} •" if d == days else label,
                callback_data=f"scanner:period:{d}",
            )
            for d, label in STATS_PERIODS.items()
        ],
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"scanner:period:{days}")],
    ]

    if mon_count > 0:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"📁 Monitoring ({mon_count})",
                    callback_data="scanner:p-status:monitoring",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton("🧭 Run Strategy", callback_data="scanner:strategy"),
            InlineKeyboardButton(
                "💼 Sell All", callback_data="scanner:sell_all_confirm"
            ),
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton("🗂 Archive", callback_data="scanner:archive"),
            InlineKeyboardButton("⚙️ Settings", callback_data="scanner:settings"),
        ]
    )
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="cancel")])

    return {
        "text": stat_text,
        "reply_markup": InlineKeyboardMarkup(keyboard),
        "disable_web_page_preview": True,
    }


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else "main"

    if action == "period":
        block = await _block(context, days=int(parts[2]))
        await query.edit_message_text(
            text=block["text"],
            reply_markup=block["reply_markup"],
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

    elif action == "refresh":
        row_id = parts[2]
        block = await handle_block(context, status_arg=row_id)
        await query.edit_message_text(
            text="🔄 *Refreshing data...*\n\n" + block["text"],
            reply_markup=block["reply_markup"],
            parse_mode="Markdown",
        )

    elif action == "rejected":
        await pairs.button_rejected(update, context)

    elif action == "status":
        await _button_status(update, context)

    elif action == "preview":
        await pairs.button_preview(update, context)

    elif action == "buy_confirm":
        await pairs.button_buy_confirm(update, context)

    elif action == "buy":
        await pairs.button_buy(update, context)

    elif action == "strategy":
        result = await run_strategy_monitor(context)
        block = await handle_block(context)
        charts = [
            [
                InlineKeyboardButton(
                    f"📈 {symbol} Chart", url=f"https://dexscreener.com/base/{pair}"
                )
            ]
            for symbol, pair in result["charts"]
        ]
        await query.edit_message_text(
            f"{block['text']}\n\n{result['text']}",
            reply_markup=InlineKeyboardMarkup(
                charts + list(block["reply_markup"].inline_keyboard)
            ),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

    elif action == "sell_all_confirm":
        await query.edit_message_text(
            "⚠️ *Sell every token the bot has bought?*\n\n"
            "This sells whatever is still in the wallet, including positions "
            "already marked completed. Tokens worth less than the gas are skipped.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("❌ Cancel", callback_data="scanner:home"),
                        InlineKeyboardButton(
                            "✅ Yes, Sell All", callback_data="scanner:sell_all"
                        ),
                    ]
                ]
            ),
        )

    elif action == "sell_all":
        await query.edit_message_text("⏳ *Selling...*", parse_mode="Markdown")
        result = await run_sell_all(context)
        block = await handle_block(context)
        await query.edit_message_text(
            f"{result}\n\n{block['text']}",
            reply_markup=block["reply_markup"],
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

    elif action == "p-status":
        await positions.button_status(update, context)

    elif action == "sell":
        await positions.button_sell(update, context)

    elif action == "remove_confirm":
        table = parts[2]
        row_id = parts[3]
        record = await pairs_get_by_id(row_id, table)
        symbol = escape_markdown((record or {}).get("symbol") or "Unknown")
        back_cb = "p-status" if table == "positions" else "status"
        action_label = (
            "close this position"
            if table == "positions"
            else "mark this pair as completed"
        )

        await query.edit_message_text(
            f"⚠️ *Are you sure you want to {action_label}?*\n\n${symbol} #{row_id}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "❌ Cancel",
                            callback_data=f"scanner:{back_cb}:{row_id}",
                        ),
                        InlineKeyboardButton(
                            "✅ Yes, Confirm",
                            callback_data=f"scanner:remove:{table}:{row_id}",
                        ),
                    ]
                ]
            ),
        )

    elif action == "remove":
        table = parts[2]
        row_id = parts[3]
        record = await pairs_get_by_id(row_id, table)
        symbol = escape_markdown((record or {}).get("symbol") or "Unknown")

        if record:
            await pairs_mark_completed(record["target_address"])

        block = await handle_block(context)
        await query.edit_message_text(
            f"✅ *#{row_id}* (${symbol}) marked as completed.\n\n" + block["text"],
            parse_mode="Markdown",
            reply_markup=block["reply_markup"],
            disable_web_page_preview=True,
        )

    elif action == "archive":
        block = await _block_archive(context)
        await query.edit_message_text(
            text=block["text"],
            reply_markup=block["reply_markup"],
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )


async def _block_archive(context: ContextTypes.DEFAULT_TYPE):
    stats, _, _, _ = await pairs_get_stats(days=1)
    stats = stats or {}
    mon_count = stats.get("active_positions", 0)
    total_comp = stats.get("completed", 0)
    awaiting = stats.get("awaiting", 0)
    rejected = sum(stats.get(s, 0) for s in PAIR_STATUS)

    text = (
        "🤖 *Token Scanner - Archive*\n"
        f" ├ Total: {stats.get('total', 0)}\n"
        f" ├ Pending: {stats.get('pending', 0)}\n"
        f" ├ Awaiting: {awaiting}\n"
        f" ├ Rejected: {rejected}\n"
        f" ├ Monitoring: {mon_count}\n"
        f" └ Completed: {total_comp}\n"
    )

    keyboard = []

    pos_buttons = []
    if mon_count > 0:
        pos_buttons.append(
            InlineKeyboardButton(
                f"📁 Monitoring ({mon_count})",
                callback_data="scanner:p-status:monitoring",
            )
        )
    if total_comp > 0:
        pos_buttons.append(
            InlineKeyboardButton(
                f"📁 Completed ({total_comp})",
                callback_data="scanner:p-status:completed",
            )
        )
    if pos_buttons:
        keyboard.append(pos_buttons)

    if rejected > 0:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"📁 Rejected ({rejected})", callback_data="scanner:rejected"
                )
            ]
        )

    if awaiting > 0:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"📁 Awaiting ({awaiting})", callback_data="scanner:status:awaiting"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "📤 Export Pairs", callback_data="scanner:export:pairs"
            ),
            InlineKeyboardButton(
                "📤 Export Positions", callback_data="scanner:export:positions"
            ),
        ]
    )
    keyboard.append(
        [InlineKeyboardButton("🔄 Refresh", callback_data="scanner:archive")]
    )
    keyboard.append(
        [InlineKeyboardButton("🔙 Back to Scanner", callback_data="scanner:home")]
    )
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="cancel")])

    return {
        "text": text,
        "reply_markup": InlineKeyboardMarkup(keyboard),
        "disable_web_page_preview": True,
    }


async def _button_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    identifier = ":".join(parts[2:]) if len(parts) > 2 else None
    block = await handle_block(context, status_arg=identifier)
    await query.edit_message_text(
        text=block["text"],
        reply_markup=block["reply_markup"],
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )

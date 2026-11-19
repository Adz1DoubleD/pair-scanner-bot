from decimal import Decimal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .. import jobs
from ..db.options import option_set
from ..db.pairs import pairs_get_by_status
from ..settings import (
    MODE_LIVE,
    MODE_NAMES,
    MODE_OFF,
    MODE_WATCH,
    OPTIONS,
    SETTINGS,
    current_mode,
    forced_mode,
)

GROUPS = {
    "monitors": (
        "🛰️ Monitors & Alerts",
        [
            "mode",
            "pairs_enabled",
            "strategy_enabled",
            "buy_alerts_enabled",
            "sell_alerts_enabled",
        ],
    ),
    "trading": (
        "💰 Trading",
        ["buy_amount_eth", "max_positions", "slippage", "min_eth_balance"],
    ),
    "audit": (
        "🛡️ Pair Audit",
        [
            "min_liquidity_eth",
            "min_ratio_percent",
            "min_lp_locked_pct",
            "audit_cooldown",
            "auto_audit_max",
            "max_audits",
        ],
    ),
    "strategy": ("🧭 Strategy", ["strat_min_liq_usd", "strat_min_momentum_h1"]),
    "selling": (
        "📉 Selling",
        [
            "take_profit_percent",
            "stop_loss_percent",
            "trailing_stop_percent",
            "rug_liq_threshold",
            "dead_token_threshold",
            "dead_token_hours",
        ],
    ),
    "schedule": (
        "⏰ Schedule & Upkeep",
        [
            "pairs_monitor_interval",
            "sell_monitor_interval",
            "strategy_monitor_interval",
            "health_check_interval",
            "db_cleanup_mb",
        ],
    ),
}

LABELS = {
    "mode": ("🎛 Mode", ""),
    "pairs_enabled": ("🤖 Pairs Monitor", ""),
    "strategy_enabled": ("🧭 Strategy Monitor", ""),
    "buy_alerts_enabled": ("🔔 Buy Alerts", ""),
    "sell_alerts_enabled": ("🔔 Sell Alerts", ""),
    "buy_amount_eth": ("Buy Amount", "ETH"),
    "max_positions": ("Max Positions", ""),
    "slippage": ("Slippage", "%"),
    "min_eth_balance": ("Minimum Balance", "ETH"),
    "min_liquidity_eth": ("Min Liquidity", "ETH"),
    "min_ratio_percent": ("Min Liquidity Ratio", "%"),
    "min_lp_locked_pct": ("Min LP Locked", "%"),
    "audit_cooldown": ("Audit Delay", "min"),
    "auto_audit_max": ("Audits per Run", ""),
    "max_audits": ("Max Retries", ""),
    "strat_min_liq_usd": ("Min Liquidity", "USD"),
    "strat_min_momentum_h1": ("Min 1h Momentum", "%"),
    "take_profit_percent": ("Take Profit", "%"),
    "stop_loss_percent": ("Stop Loss", "%"),
    "trailing_stop_percent": ("Trailing Stop", "%"),
    "rug_liq_threshold": ("Rug Liquidity Drop", "%"),
    "dead_token_threshold": ("Dead Token Move", "%"),
    "dead_token_hours": ("Dead Token After", "h"),
    "pairs_monitor_interval": ("Pairs Monitor", "min"),
    "sell_monitor_interval": ("Sell Monitor", "min"),
    "strategy_monitor_interval": ("Strategy Monitor", "min"),
    "health_check_interval": ("Health Check", "min"),
    "db_cleanup_mb": ("Auto-clean Database At", "MB"),
}

LIMITS = {
    "buy_amount_eth": (0.00001, 10),
    "max_positions": (1, 50),
    "slippage": (0, 50),
    "min_eth_balance": (0, 10),
    "min_liquidity_eth": (0, 1000),
    "min_ratio_percent": (1, 100),
    "min_lp_locked_pct": (0, 100),
    "audit_cooldown": (1, 10080),
    "auto_audit_max": (1, 100),
    "max_audits": (1, 100),
    "strat_min_liq_usd": (0, None),
    "strat_min_momentum_h1": (0, 100),
    "take_profit_percent": (1, 1000),
    "stop_loss_percent": (1, 100),
    "trailing_stop_percent": (1, 100),
    "rug_liq_threshold": (1, 100),
    "dead_token_threshold": (0.1, 10),
    "dead_token_hours": (1, 168),
    "pairs_monitor_interval": (1, 10080),
    "sell_monitor_interval": (1, 10080),
    "strategy_monitor_interval": (1, 10080),
    "health_check_interval": (1, 10080),
    "db_cleanup_mb": (0.5, 1000),
}

TOGGLES = {
    "pairs_enabled",
    "strategy_enabled",
    "buy_alerts_enabled",
    "sell_alerts_enabled",
}
GROUP_OF = {name: g for g, (_, names) in GROUPS.items() for name in names}
TO_SETTINGS = InlineKeyboardButton("🔙 Settings", callback_data="scanner:settings")


MODE_HELP = {
    MODE_OFF: "nothing runs: no scanning, no buys, no sells, no alerts.",
    MODE_WATCH: "finds and audits pairs and says what it would buy, but never "
    "buys. Open positions are still sold on your rules.",
    MODE_LIVE: "buys and sells with your wallet.",
}


def settings_menu():
    mode = current_mode()
    text = (
        "⚙️ *Scanner Settings*\n\n"
        + f"{MODE_NAMES[mode]}: {MODE_HELP[mode]}"
        + (
            "\n_Set for this run only, by the bot's start-up settings._"
            if forced_mode() is not None
            else ""
        )
        + "\n\nPick a section:"
    )
    kb = [
        [InlineKeyboardButton(title, callback_data=f"scanner:settings:g:{g}")]
        for g, (title, _) in GROUPS.items()
    ]
    kb.append([InlineKeyboardButton("🔙 Scanner", callback_data="scanner:home")])
    return text, InlineKeyboardMarkup(kb)


def group_screen(group, status=None):
    title, names = GROUPS[group]
    kb = []
    for name in names:
        label, unit = LABELS[name]
        value = OPTIONS.get(name)
        if name == "mode":
            kb.append(
                [
                    InlineKeyboardButton(
                        f"{label}: {MODE_NAMES[current_mode()]}",
                        callback_data="scanner:settings:m",
                    )
                ]
            )
        elif name in TOGGLES:
            shown = f"{'✅' if int(value) else '❌'} {label}"
            kb.append(
                [
                    InlineKeyboardButton(
                        shown, callback_data=f"scanner:settings:t:{name}"
                    )
                ]
            )
        else:
            shown = f"{label}: {Decimal(str(value)).normalize():f} {unit}".rstrip()
            kb.append(
                [
                    InlineKeyboardButton(
                        f"✏️ {shown}", callback_data=f"scanner:settings:e:{name}"
                    )
                ]
            )
    kb.append([TO_SETTINGS])
    text = (
        f"{status}\n\n" if status else ""
    ) + f"*{title}*\n\nTap a setting to change it."
    return text, InlineKeyboardMarkup(kb)


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    bits = query.data.split(":")
    action = bits[2] if len(bits) > 2 else ""
    name = bits[3] if len(bits) > 3 else ""
    context.user_data.pop("state", None)

    if action == "g" and name in GROUPS:
        await query.answer()
        text, markup = group_screen(name)

    elif action == "m":
        await query.answer()
        saved = int(OPTIONS.get("mode", MODE_WATCH))
        text = "🎛 *Mode*\n\n" + "\n".join(
            f"{MODE_NAMES[m]}: {MODE_HELP[m]}"
            for m in (MODE_OFF, MODE_WATCH, MODE_LIVE)
        )
        if forced_mode() is not None:
            text += (
                f"\n\n_This run is set to {MODE_NAMES[forced_mode()]} by the bot's "
                "start-up settings, so a change here applies from the next normal start._"
            )
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        ("✅ " if m == saved else "") + MODE_NAMES[m],
                        callback_data=f"scanner:settings:set:{m}",
                    )
                    for m in (MODE_OFF, MODE_WATCH, MODE_LIVE)
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Monitors & Alerts",
                        callback_data="scanner:settings:g:monitors",
                    )
                ],
            ]
        )

    elif action == "set" and name in ("0", "1", "2"):
        target = int(name)
        if target == MODE_LIVE:
            if not SETTINGS.private_key:
                await query.answer(
                    f"Set {SETTINGS.env_prefix}PRIVATE_KEY before switching to Live.",
                    show_alert=True,
                )
                return
            await query.answer()
            text = (
                "⚠️ *Switch to 💰 Live?*\n\n"
                f"The bot will spend up to `{Decimal(str(OPTIONS['buy_amount_eth'])).normalize():f} ETH` per "
                f"buy, on up to {OPTIONS['max_positions']} tokens at a time, from "
                "your wallet. New tokens are extremely risky: most fail and many "
                "are scams. Only use money you can afford to lose."
            )
            markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "❌ Cancel", callback_data="scanner:settings:m"
                        ),
                        InlineKeyboardButton(
                            "✅ Go Live", callback_data="scanner:settings:on:live"
                        ),
                    ]
                ]
            )
        else:
            await option_set("mode", target)
            if jobs.application is not None:
                jobs.start_jobs(jobs.application)
            held = len(
                await pairs_get_by_status(status="monitoring", table="positions") or []
            )
            status = f"{MODE_NAMES[target]}: {MODE_HELP[target]}"
            if held and target == MODE_WATCH:
                status += (
                    f"\n\nYou hold {held} open position{'s' * (held != 1)}. Watch "
                    "keeps selling them on your stop-loss and take-profit. Switch "
                    "to ⏸️ Off to stop everything."
                )
            elif held:
                status += (
                    f"\n\n⚠️ You hold {held} open position{'s' * (held != 1)}. "
                    "Their stop-losses and take-profits stop until you switch back."
                )
            await query.answer(f"{MODE_NAMES[target]} saved")
            text, markup = group_screen("monitors", status=status)

    elif action == "t" and name in TOGGLES:
        turning_on = not int(OPTIONS.get(name, 0))
        await option_set(name, int(turning_on))
        if name in jobs.TRADING_FLAGS and jobs.application is not None:
            jobs.apply_toggle(jobs.application, name, bool(turning_on))
        await query.answer("✅ Saved")
        text, markup = group_screen(GROUP_OF[name])

    elif action == "on" and name == "live":
        if not SETTINGS.private_key:
            await query.answer("No private key is set.", show_alert=True)
            return
        await option_set("mode", MODE_LIVE)
        if jobs.application is not None:
            jobs.start_jobs(jobs.application)
        await query.answer("💰 Live")
        text, markup = group_screen(
            "monitors", status=f"{MODE_NAMES[MODE_LIVE]}: {MODE_HELP[MODE_LIVE]}"
        )

    elif action == "e" and name in LABELS and name not in TOGGLES and name != "mode":
        await query.answer()
        label, unit = LABELS[name]
        context.user_data["state"] = f"scanner:settings:{name}"
        context.user_data["last_msg_id"] = query.message.message_id
        text = (
            f"✏️ *{label}*\n\nNow: `{Decimal(str(OPTIONS[name])).normalize():f}` {unit}\n\n"
            f"Send the new value as a number ("
            + (
                f"{Decimal(str(LIMITS[name][0])).normalize():f}–{Decimal(str(LIMITS[name][1])).normalize():f}"
                if LIMITS[name][1] is not None
                else f"{Decimal(str(LIMITS[name][0])).normalize():f} or more"
            )
            + "):"
        )
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data=f"scanner:settings:g:{GROUP_OF[name]}",
                    )
                ]
            ]
        )

    else:
        await query.answer()
        text, markup = settings_menu()

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text):
    name = context.user_data.pop("state").split(":")[2]
    chat_id = update.effective_chat.id
    msg_id = context.user_data.get("last_msg_id")

    try:
        value = float(text.replace(",", "").strip())
    except ValueError:
        value = None
    low, high = LIMITS[name]
    label, unit = LABELS[name]
    if value is None or value < low or (high is not None and value > high):
        context.user_data["state"] = f"scanner:settings:{name}"
        allowed = (
            f"between {Decimal(str(low)).normalize():f} and {Decimal(str(high)).normalize():f}"
            if high is not None
            else f"{Decimal(str(low)).normalize():f} or more"
        )
        unit = unit if unit in ("", "%") else f" {unit}"
        reply = f"⚠️ {label} must be {allowed}{unit}. Send it again:"
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data=f"scanner:settings:g:{GROUP_OF[name]}",
                    )
                ]
            ]
        )
    else:
        saved = await option_set(name, value)
        if name.endswith("_interval") and jobs.application is not None:
            jobs.apply_interval(jobs.application, name, float(saved))
        reply, markup = group_screen(
            GROUP_OF[name],
            status=f"✅ {label} set to `{Decimal(str(saved)).normalize():f}` {unit}".rstrip(),
        )

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=reply,
            parse_mode="Markdown",
            reply_markup=markup,
        )
    except Exception:
        await context.bot.send_message(
            chat_id, reply, parse_mode="Markdown", reply_markup=markup
        )

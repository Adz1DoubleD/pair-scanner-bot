from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .. import jobs
from ..db.options import option_set
from ..settings import OPTIONS, SETTINGS

GROUPS = {
    "monitors": (
        "🛰️ Monitors & Alerts",
        [
            "trading_enabled",
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
        "⏰ Schedule",
        [
            "pairs_monitor_interval",
            "sell_monitor_interval",
            "strategy_monitor_interval",
            "health_check_interval",
        ],
    ),
}

LABELS = {
    "trading_enabled": ("💸 Trading", ""),
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
}

TOGGLES = {
    "trading_enabled",
    "pairs_enabled",
    "strategy_enabled",
    "buy_alerts_enabled",
    "sell_alerts_enabled",
}
GROUP_OF = {name: g for g, (_, names) in GROUPS.items() for name in names}
TO_SETTINGS = InlineKeyboardButton("🔙 Settings", callback_data="scanner:settings")


def settings_menu():
    trading = int(OPTIONS.get("trading_enabled", 0))
    text = (
        "⚙️ *Scanner Settings*\n\n"
        + (
            "💸 Trading is *ON*: the bot buys and sells with your wallet."
            if trading
            else "👀 *Watch-only*: the bot finds and audits pairs but never buys."
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
        if name in TOGGLES:
            shown = f"{'✅' if int(value) else '❌'} {label}"
            kb.append(
                [
                    InlineKeyboardButton(
                        shown, callback_data=f"scanner:settings:t:{name}"
                    )
                ]
            )
        else:
            shown = f"{label}: {value:g} {unit}".rstrip()
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

    elif action == "t" and name in TOGGLES:
        turning_on = not int(OPTIONS.get(name, 0))
        if name == "trading_enabled" and turning_on:
            if not SETTINGS.private_key:
                await query.answer(
                    "Set SCANNER_PRIVATE_KEY before turning trading on.",
                    show_alert=True,
                )
                return
            await query.answer()
            text = (
                "⚠️ *Turn trading on?*\n\n"
                f"The bot will spend up to `{OPTIONS['buy_amount_eth']:g} ETH` per "
                f"buy, on up to {OPTIONS['max_positions']} tokens at a time, from "
                "your wallet. New tokens are extremely risky: most fail and many "
                "are scams. Only use money you can afford to lose."
            )
            markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "❌ Cancel", callback_data="scanner:settings:g:monitors"
                        ),
                        InlineKeyboardButton(
                            "✅ Turn On", callback_data="scanner:settings:on:trading"
                        ),
                    ]
                ]
            )
        else:
            await option_set(name, int(turning_on))
            if name in jobs.TRADING_FLAGS and jobs.application is not None:
                jobs.apply_toggle(jobs.application, name, bool(turning_on))
            await query.answer("✅ Saved")
            text, markup = group_screen(GROUP_OF[name])

    elif action == "on" and name == "trading":
        if not SETTINGS.private_key:
            await query.answer("No private key is set.", show_alert=True)
            return
        await option_set("trading_enabled", 1)
        await query.answer("💸 Trading is on")
        text, markup = group_screen("monitors", status="💸 *Trading is on.*")

    elif action == "e" and name in LABELS and name not in TOGGLES:
        await query.answer()
        label, unit = LABELS[name]
        context.user_data["state"] = f"scanner:settings:{name}"
        context.user_data["last_msg_id"] = query.message.message_id
        text = (
            f"✏️ *{label}*\n\nNow: `{OPTIONS[name]:g}` {unit}\n\n"
            "Send the new value as a number:"
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
    minimum = 1 if name.endswith("_interval") else 0
    if value is None or value < minimum:
        context.user_data["state"] = f"scanner:settings:{name}"
        reply = f"⚠️ That needs to be a number of at least {minimum}. Send it again:"
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
        label, unit = LABELS[name]
        reply, markup = group_screen(
            GROUP_OF[name], status=f"✅ {label} set to `{saved:g}` {unit}".rstrip()
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

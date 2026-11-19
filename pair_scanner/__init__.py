import csv
import io
import logging
import os
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version

import sentry_sdk
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import jobs
from .blocks import scanner as scanner_screen
from .blocks import settings as settings_screen
from .blocks.balances import balances_block
from .db import close_db, init_scanner_db
from .db.pairs import pairs_get_by_status
from .health import scanner_health
from .networks import refresh_networks
from .settings import (
    ENV_VARS,
    MODE_NAMES,
    SETTINGS,
    current_mode,
    forced_mode,
    is_owner,
    setup,
)
from .w3 import close_w3

try:
    __version__ = version("pair-scanner-bot")
except PackageNotFoundError:
    __version__ = "0.0.0"

NOISE = (
    "httpx.ReadError",
    "RemoteProtocolError",
    "Timed out",
    "Query is too old",
    "query id is invalid",
    "Message is not modified",
    "Conflict: terminated by other getUpdates",
)

__all__ = [
    "__version__",
    "balances_block",
    "handle_button",
    "handle_text_input",
    "scanner_block",
    "scanner_health",
    "start_scanner_bot",
    "stop_scanner_bot",
]


async def scanner_block(context: ContextTypes.DEFAULT_TYPE, brief=False):
    return await scanner_screen.handle_block(context, brief=brief)


async def home_block():
    text = (
        "🎯 *Pair Scanner*\n\n"
        f"🤖 Pairs: {MODE_NAMES[current_mode('pairs')]}\n"
        f"🧭 Strategy: {MODE_NAMES[current_mode('strategy')]}"
        + (
            f"\n_Capped at {MODE_NAMES[forced_mode()]} for this run_"
            if forced_mode() is not None
            else ""
        )
    )
    return {
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🎯 Scanner", callback_data="scanner:home"),
                    InlineKeyboardButton(
                        "₿ Balances", callback_data="scanner:balances"
                    ),
                ],
                [InlineKeyboardButton("⚙️ Settings", callback_data="scanner:settings")],
                [InlineKeyboardButton("❌ Close", callback_data="cancel")],
            ]
        ),
    }


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if not is_owner(query.from_user.id):
        await query.answer("This bot only answers its owner.", show_alert=True)
        return

    if data.startswith("scanner:settings"):
        await settings_screen.handle_button(update, context)
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

    if data in ("scanner:menu", "scanner:home", "scanner:balances"):
        if data == "scanner:menu":
            block = await home_block()
        elif data == "scanner:home":
            block = await scanner_screen.handle_block(context)
        else:
            block = await balances_block()
        await query.edit_message_text(
            block["text"],
            reply_markup=block.get("reply_markup"),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return

    await scanner_screen.handle_button(update, context)


async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await settings_screen.handle_text(update, context, text)


async def command_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("This bot only answers its owner.")
        return
    context.user_data.pop("state", None)
    block = await home_block()
    await update.message.reply_text(
        block["text"], reply_markup=block["reply_markup"], parse_mode="Markdown"
    )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if any(n.lower() in str(context.error).lower() for n in NOISE):
        return
    sentry_sdk.capture_exception(context.error)


async def start_scanner_bot(share_with=None, env_prefix=None, **options):
    if env_prefix is None:
        env_prefix = "" if share_with is None else "SCANNER_"
    setup(env_prefix, **options)
    required = ("owner_id", "db_url", "wallet_address")
    if share_with is None:
        required = ("token", *required)
    missing = [
        f"{env_prefix}{ENV_VARS[name]}"
        for name in required
        if not getattr(SETTINGS, name)
    ]
    if missing:
        raise ValueError(f"Pair scanner needs these settings: {', '.join(missing)}")
    if not SETTINGS.alerts_chat_id:
        SETTINGS.alerts_chat_id = SETTINGS.owner_id
    refresh_networks()

    os.environ.setdefault("PTB_TIMEDELTA", "1")
    for name in ("telegram", "httpx"):
        logging.getLogger(name).addFilter(
            lambda record: not any(n in record.getMessage() for n in NOISE)
        )

    await init_scanner_db()

    mode = (
        f"Pairs {MODE_NAMES[current_mode('pairs')]}, "
        f"Strategy {MODE_NAMES[current_mode('strategy')]}"
        + (", for this run" if forced_mode() is not None else "")
    )
    if share_with is not None and not SETTINGS.token:
        jobs.application = share_with
        jobs.shares_host = True
        jobs.start_jobs(share_with)
        print(f"🚀 Pair scanner running on the host's bot ({mode})")
        return share_with

    application = Application.builder().token(SETTINGS.token).build()
    application.add_handler(CommandHandler("start", command_start))
    application.add_handler(
        CallbackQueryHandler(handle_button, pattern=r"^(scanner:|cancel$|ignore$)")
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.CHANNEL_POSTS,
            handle_text_input,
        )
    )
    application.add_error_handler(error_handler)
    if share_with is not None:
        application.bot_data["host"] = share_with

    await application.initialize()
    await application.start()
    jobs.application = application
    jobs.shares_host = False
    jobs.start_jobs(application)
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    print(
        f"🚀 Pair scanner running on its own bot @{application.bot.username} ({mode})"
    )
    return application


async def stop_scanner_bot():
    application = jobs.application
    if application is not None:
        await jobs.stop_jobs(application)
        if jobs.shares_host:
            for name in jobs.JOBS:
                for job in application.job_queue.get_jobs_by_name(name):
                    job.schedule_removal()
        elif application.updater and application.updater.running:
            await application.updater.stop()
        if not jobs.shares_host and application.running:
            await application.stop()
        if not jobs.shares_host and application._initialized:
            await application.shutdown()
        jobs.application = None
        jobs.shares_host = False
    await close_w3()
    await close_db()

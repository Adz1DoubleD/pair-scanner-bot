"""Pair scanner: start it on its own or inside a host bot, and the functions a host can use."""

import logging
import os
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import sentry_sdk
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import jobs
from .blocks import dashboard
from .blocks.balances import balances_block
from .buttons import handle_button, menu_block
from .db import close_db, init_scanner_db
from .health import scanner_health
from .networks import refresh_networks
from .settings import (
    ENV_VARS,
    MODE_LIVE,
    MODE_NAMES,
    MODE_OFF,
    MODE_WATCH,
    MONITORS,
    SETTINGS,
    current_mode,
    forced_mode,
    is_owner,
    setup,
)
from .text_input import handle_text_input
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
    "scanner_modes",
    "start_scanner_bot",
    "stop_scanner_bot",
]


def scanner_modes() -> dict[str, str]:
    """Each monitor's mode for this run, as {"pairs": "watch", "strategy": "off"}.

    Values are "off", "watch" or "live", after any cap set at start-up.
    """
    names = {MODE_OFF: "off", MODE_WATCH: "watch", MODE_LIVE: "live"}
    return {monitor: names[current_mode(monitor)] for monitor in MONITORS}


async def scanner_block(
    context: ContextTypes.DEFAULT_TYPE, brief: bool = False
) -> dict:
    """The scanner dashboard as a block: {"text": ..., "reply_markup": ...}.

    With brief=True it returns only the stats text, for a host's daily summary.
    """
    return await dashboard.handle_block(context, brief=brief)


async def start_scanner_bot(
    share_with: Application | None = None,
    env_prefix: str | None = None,
    **options: Any,
) -> Application:
    """Start the scanner and return the Application it runs on.

    share_with: a host's Application. Without a bot token the scanner then runs
    its jobs on the host's bot; with one it runs its own bot alongside.
    env_prefix: prefix for its environment variables, "SCANNER_" inside a host
    and none on its own.
    options: settings that override the environment, such as owner_id,
    alerts_chat_id, timezone or mode="off".
    """
    if env_prefix is None:
        env_prefix = "" if share_with is None else "SCANNER_"
    setup(env_prefix, **options)
    required: tuple[str, ...] = ("owner_id", "db_url", "wallet_address")
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
    application.add_handler(CommandHandler("start", _command_start))
    application.add_handler(
        CallbackQueryHandler(handle_button, pattern=r"^(scanner:|cancel$|ignore$)")
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.CHANNEL_POSTS,
            handle_text_input,
        )
    )
    application.add_error_handler(_error_handler)
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


async def stop_scanner_bot() -> None:
    """Stop the scanner's jobs, and its own bot if it started one."""
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


async def _command_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("This bot only answers its owner.")
        return
    context.user_data.pop("state", None)
    block = await menu_block()
    await update.message.reply_text(
        block["text"], reply_markup=block["reply_markup"], parse_mode="Markdown"
    )


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if any(n.lower() in str(context.error).lower() for n in NOISE):
        return
    sentry_sdk.capture_exception(context.error)

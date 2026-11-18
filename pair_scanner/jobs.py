import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import sentry_sdk
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from .db.options import option_set
from .db.pairs import clear_archive, db_total_mb
from .health import scanner_health
from .helpers import close_kb
from .monitors import run_pairs_monitor, run_sell_monitor, run_strategy_monitor
from .settings import OPTIONS, SETTINGS
from .watchers import watch_new_pairs, watch_pair_metadata

application = None
shares_host = False
DEFAULT_INTERVAL_MINUTES = 60
LIVE_FIRST_RUN_SECONDS = 60
TRADING_FLAGS = ("pairs_enabled", "strategy_enabled")


async def send_buys(context: ContextTypes.DEFAULT_TYPE):
    if context.job.name == "Pairs Monitor":
        result = await run_pairs_monitor(context)
        if isinstance(result, str):
            return
        source = "Pairs"
    else:
        result = await run_strategy_monitor(context)
        source = "Strategy"

    result = result or {}
    buys, failed = result.get("buys", []), result.get("failed", [])
    watched = result.get("watched", [])
    if not (buys or failed or watched) or not int(OPTIONS.get("buy_alerts_enabled", 1)):
        return

    sections = []
    if buys:
        sections.append(
            f"🛒 *{source} Buy*\n"
            + "\n".join(
                f" {'└' if i == len(buys) - 1 else '├'} {line}"
                for i, line in enumerate(buys)
            )
        )
    if watched:
        sections.append(
            f"👀 *{source}: would buy* (watch-only)\n"
            + "\n".join(
                f" {'└' if i == len(watched) - 1 else '├'} {line}"
                for i, line in enumerate(watched)
            )
        )
    if failed:
        sections.append(
            f"⚠️ *{source} Buy Failed*\n"
            + "\n".join(
                f" {'└' if i == len(failed) - 1 else '├'} {line}"
                for i, line in enumerate(failed)
            )
        )
    text = "\n\n".join(sections)
    if result.get("positions") and buys:
        held, max_positions = result["positions"]
        text += f"\n\n💼 Positions: {held}/{max_positions}"

    await context.bot.send_message(
        chat_id=SETTINGS.alerts_chat_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"📈 {symbol} Chart",
                        url=f"https://dexscreener.com/base/{pair}",
                    )
                ]
                for symbol, pair in result.get("charts", [])
            ]
            + list(close_kb().inline_keyboard)
        ),
    )


async def send_sells(context: ContextTypes.DEFAULT_TYPE):
    result = await run_sell_monitor(context) or {}
    sells = result.get("sells", [])
    if not sells or not int(OPTIONS.get("sell_alerts_enabled", 1)):
        return

    text = "💰 *Sell*\n" + "\n".join(
        f" {'└' if i == len(sells) - 1 else '├'} {line}" for i, line in enumerate(sells)
    )
    if result.get("positions"):
        held, max_positions = result["positions"]
        text += f"\n\n💼 Positions: {held}/{max_positions}"

    await context.bot.send_message(
        chat_id=SETTINGS.alerts_chat_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"📈 {symbol} Chart",
                        url=f"https://dexscreener.com/base/{pair}",
                    )
                ]
                for symbol, pair in result.get("charts", [])
            ]
            + list(close_kb().inline_keyboard)
        ),
    )


async def send_health(context: ContextTypes.DEFAULT_TYPE):
    cleaned = []
    try:
        before = await db_total_mb()
        limit = float(OPTIONS.get("db_cleanup_mb") or 0)
        if limit and before >= limit:
            await clear_archive()
            after = await db_total_mb()
            cleaned.append(
                f"🧹 *Database auto-cleaned* — {before:.2f} → {after:.2f} MB"
            )
    except Exception as e:
        sentry_sdk.capture_exception(e)

    report = await scanner_health()
    alerts = cleaned + list(report["alerts"])

    if report["low_eth"]:
        for flag in TRADING_FLAGS:
            try:
                await option_set(flag, 0)
            except Exception as e:
                sentry_sdk.capture_exception(e)
                OPTIONS[flag] = 0
            apply_toggle(context.application, flag, enabled=False)
        alerts.append("🤖 *Scanner & Strategy Disabled* — insufficient ETH balance")

    if alerts:
        await context.bot.send_message(
            chat_id=SETTINGS.alerts_chat_id,
            text="🚨 *SCANNER HEALTH ALERT*\n\n" + "\n".join(f"• {a}" for a in alerts),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                (
                    [
                        [
                            InlineKeyboardButton(
                                "🔧 Open database dashboard",
                                url=SETTINGS.db_dashboard_url,
                            )
                        ]
                    ]
                    if report.get("db_problem") and SETTINGS.db_dashboard_url
                    else []
                )
                + list(close_kb().inline_keyboard)
            ),
        )


JOBS = {
    "New Pairs Watcher": {
        "kind": "loop",
        "callback": watch_new_pairs,
        "flags": ("pairs_enabled",),
    },
    "Pair Metadata Watcher": {
        "kind": "loop",
        "callback": watch_pair_metadata,
        "flags": ("pairs_enabled",),
    },
    "Pairs Monitor": {
        "kind": "repeating",
        "callback": send_buys,
        "flags": ("pairs_enabled",),
        "interval": "pairs_monitor_interval",
        "first": 240,
    },
    "Sell Monitor": {
        "kind": "repeating",
        "callback": send_sells,
        "flags": ("pairs_enabled", "strategy_enabled"),
        "interval": "sell_monitor_interval",
        "first": 360,
    },
    "Strategy Monitor": {
        "kind": "repeating",
        "callback": send_buys,
        "flags": ("strategy_enabled",),
        "interval": "strategy_monitor_interval",
        "first": 480,
    },
    "Scanner Health": {
        "kind": "repeating",
        "callback": send_health,
        "flags": (),
        "interval": "health_check_interval",
        "first": 600,
    },
}


def set_job(name, application, enabled=None, minutes=None, first=None):
    cfg = JOBS[name]
    bg_tasks = application.bot_data.setdefault("scanner_bg_tasks", set())
    job_queue = application.job_queue

    if cfg["kind"] == "loop":
        for t in list(bg_tasks):
            if t.get_name() == name:
                t.cancel()
                bg_tasks.discard(t)
    else:
        for job in job_queue.get_jobs_by_name(name):
            job.schedule_removal()

    if enabled is None:
        enabled = not cfg["flags"] or any(
            int(OPTIONS.get(f, 0) or 0) == 1 for f in cfg["flags"]
        )
    if not enabled:
        return False

    if cfg["kind"] == "loop":
        callback = cfg["callback"]

        async def keep_running():
            while True:
                try:
                    await callback()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    print(f"❌ Task Crashed: {name} - Error: {e} — restarting in 30s")
                    sentry_sdk.capture_exception(e)
                    await asyncio.sleep(30)
                else:
                    await asyncio.sleep(1)

        task = asyncio.create_task(keep_running(), name=name)
        bg_tasks.add(task)
        task.add_done_callback(bg_tasks.discard)
        return True

    if minutes is None:
        minutes = float(OPTIONS.get(cfg["interval"]) or DEFAULT_INTERVAL_MINUTES)
    if minutes <= 0:
        return False
    job_queue.run_repeating(
        cfg["callback"],
        interval=minutes * 60,
        first=cfg["first"] if first is None else first,
        name=name,
    )
    return True


def apply_toggle(application, flag, enabled):
    running_tasks = {
        t.get_name() for t in application.bot_data.setdefault("scanner_bg_tasks", set())
    }
    for name, cfg in JOBS.items():
        if flag not in cfg["flags"]:
            continue
        should_run = any(
            enabled if f == flag else int(OPTIONS.get(f, 0) or 0) == 1
            for f in cfg["flags"]
        )
        if cfg["kind"] == "loop":
            running = name in running_tasks
        else:
            running = bool(application.job_queue.get_jobs_by_name(name))
        if should_run != running:
            set_job(name, application, enabled=should_run, first=LIVE_FIRST_RUN_SECONDS)


def apply_interval(application, interval_setting, minutes):
    for name, cfg in JOBS.items():
        if cfg.get("interval") == interval_setting:
            return name, set_job(
                name, application, minutes=minutes, first=LIVE_FIRST_RUN_SECONDS
            )
    return None


def start_jobs(application):
    for name in JOBS:
        set_job(name, application)


async def stop_jobs(application):
    tasks = list(application.bot_data.setdefault("scanner_bg_tasks", set()))
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def job_status(application, job_name):
    jobs = application.job_queue.get_jobs_by_name(job_name) if application else []
    if not jobs or jobs[0].next_t is None:
        flags = JOBS.get(job_name, {}).get("flags")
        if flags and not any(int(OPTIONS.get(f, 0) or 0) == 1 for f in flags):
            return "⏸️ Disabled", "--"
        return "Not Scheduled", "--"

    tz = ZoneInfo(SETTINGS.timezone)
    next_run = jobs[0].next_t.astimezone(tz)
    total_seconds = int((next_run - datetime.now(tz)).total_seconds())
    if total_seconds < 0:
        return next_run.strftime("%H:%M"), "Soon..."
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return next_run.strftime("%H:%M"), f"{hours}h {minutes}m"

"""Scheduling the monitors and watchers, and sending their alerts."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import sentry_sdk
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes

from .db.options import option_set
from .db.pairs import clear_archive, db_total_mb
from .health import scanner_health
from .helpers import close_kb
from .settings import (
    MODE_LIVE,
    MODE_OFF,
    MODE_WATCH,
    MONITORS,
    OPTIONS,
    SETTINGS,
    current_mode,
)
from .trading.monitors import run_pairs_monitor, run_sell_monitor
from .trading.strategy import run_strategy_monitor
from .trading.watchers import watch_new_pairs, watch_pair_metadata

application: Application | None = None
shares_host = False
DEFAULT_INTERVAL_MINUTES = 60
LIVE_FIRST_RUN_SECONDS = 60
REMIND_AFTER = timedelta(hours=12)
REPORTED: dict[str, dict[str, Any]] = {}


async def _send_buys(context: ContextTypes.DEFAULT_TYPE) -> None:
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
    if not int(OPTIONS.get("buy_alerts_enabled", 1)):
        buys = []
        result["charts"] = []
    if not (buys or failed or watched):
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


async def _send_sells(context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await run_sell_monitor(context) or {}
    sells = (
        result.get("sells", []) if int(OPTIONS.get("profit_alerts_enabled", 1)) else []
    )
    losses = (
        result.get("losses", []) if int(OPTIONS.get("loss_alerts_enabled", 1)) else []
    )
    paper = result.get("paper", [])
    if not (sells or losses or paper):
        return

    text = "\n\n".join(
        title
        + "\n"
        + "\n".join(
            f" {'└' if i == len(lines) - 1 else '├'} {line}"
            for i, line in enumerate(lines)
        )
        for title, lines in (
            ("💰 *Sell*", sells),
            ("🚨 *Lost*", losses),
            ("👀 *Would have sold* (watch-only)", paper),
        )
        if lines
    )
    if result.get("positions") and (sells or losses):
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


async def _send_health(context: ContextTypes.DEFAULT_TYPE) -> None:
    cleaned = []
    try:
        before = await db_total_mb()
        limit = float(OPTIONS.get("db_cleanup_mb") or 0)
        if limit and before >= limit:
            removed = await clear_archive()
            after = await db_total_mb()
            cleaned.append(
                f"🧹 *Database auto-cleaned* — {before:.2f} → {after:.2f} MB, "
                f"{removed} rejected pairs and old trades removed "
                f"(limit {limit:g} MB)"
            )
    except Exception as e:
        sentry_sdk.capture_exception(e)

    report = await scanner_health()

    events = list(cleaned)
    if report["low_eth"]:
        for monitor in MONITORS:
            if current_mode(monitor) != MODE_LIVE:
                continue
            try:
                await option_set(f"{monitor}_mode", MODE_WATCH)
            except Exception as e:
                sentry_sdk.capture_exception(e)
                OPTIONS[f"{monitor}_mode"] = MODE_WATCH
        apply_modes(context.application)
        events.append("👀 *Switched to Watch* — insufficient ETH balance")

    now = datetime.now(timezone.utc)
    current = {alert.split(":")[0]: alert for alert in report["alerts"]}
    new = [alert for key, alert in current.items() if key not in REPORTED]
    fixed = [
        (key, now - REPORTED.pop(key)["since"])
        for key in list(REPORTED)
        if key not in current
    ]
    reminders = []
    for key, alert in current.items():
        seen = REPORTED.setdefault(key, {"since": now, "reminded": False})
        if not seen["reminded"] and now - seen["since"] >= REMIND_AFTER:
            seen["reminded"] = True
            reminders.append(alert)

    sections = []
    if events or new:
        sections.append(
            "🚨 *SCANNER HEALTH ALERT*\n\n"
            + "\n".join(f"• {alert}" for alert in events + new)
        )
    if reminders:
        sections.append(
            "⏰ *STILL A PROBLEM* (over 12 hours)\n\n"
            + "\n".join(f"• {alert}" for alert in reminders)
        )
    if fixed:
        sections.append(
            "✅ *SCANNER HEALTH RECOVERED*\n\n"
            + "\n".join(
                f"• {key} is fine again, after "
                f"{int(gone.total_seconds() // 3600)}h "
                f"{int(gone.total_seconds() % 3600 // 60)}m"
                for key, gone in fixed
            )
        )

    if sections:
        await context.bot.send_message(
            chat_id=SETTINGS.alerts_chat_id,
            text="\n\n".join(sections),
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


JOBS: dict[str, dict[str, Any]] = {
    "New Pairs Watcher": {
        "kind": "loop",
        "callback": watch_new_pairs,
        "monitor": "pairs",
    },
    "Pair Metadata Watcher": {
        "kind": "loop",
        "callback": watch_pair_metadata,
        "monitor": "pairs",
    },
    "Pairs Monitor": {
        "kind": "repeating",
        "callback": _send_buys,
        "monitor": "pairs",
        "interval": "pairs_monitor_interval",
        "first": 240,
    },
    "Sell Monitor": {
        "kind": "repeating",
        "callback": _send_sells,
        "interval": "sell_monitor_interval",
        "first": 360,
    },
    "Strategy Monitor": {
        "kind": "repeating",
        "callback": _send_buys,
        "monitor": "strategy",
        "interval": "strategy_monitor_interval",
        "first": 480,
    },
    "Scanner Health": {
        "kind": "repeating",
        "callback": _send_health,
        "interval": "health_check_interval",
        "first": 600,
    },
}


def apply_modes(application: Application) -> None:
    running_tasks = {
        t.get_name() for t in application.bot_data.setdefault("scanner_bg_tasks", set())
    }
    for name, cfg in JOBS.items():
        should_run = any(
            current_mode(m) != MODE_OFF
            for m in ((cfg["monitor"],) if cfg.get("monitor") else MONITORS)
        )
        if cfg["kind"] == "loop":
            running = name in running_tasks
        else:
            running = bool(application.job_queue.get_jobs_by_name(name))
        if should_run != running:
            _set_job(
                name, application, enabled=should_run, first=LIVE_FIRST_RUN_SECONDS
            )


def apply_interval(
    application: Application, interval_setting: str, minutes: float
) -> tuple[str, bool] | None:
    for name, cfg in JOBS.items():
        if cfg.get("interval") == interval_setting:
            return name, _set_job(
                name, application, minutes=minutes, first=LIVE_FIRST_RUN_SECONDS
            )
    return None


def start_jobs(application: Application) -> None:
    for name in JOBS:
        _set_job(name, application)


async def stop_jobs(application: Application) -> None:
    tasks = list(application.bot_data.setdefault("scanner_bg_tasks", set()))
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def job_status(application: Application | None, job_name: str) -> tuple[str, str]:
    jobs = application.job_queue.get_jobs_by_name(job_name) if application else []
    if not jobs or jobs[0].next_t is None:
        monitor = JOBS.get(job_name, {}).get("monitor")
        if monitor and current_mode(monitor) == MODE_OFF:
            return "⏸️ Off", "--"
        return "Not Scheduled", "--"

    tz = ZoneInfo(SETTINGS.timezone)
    next_run = jobs[0].next_t.astimezone(tz)
    total_seconds = int((next_run - datetime.now(tz)).total_seconds())
    if total_seconds < 0:
        return next_run.strftime("%H:%M"), "Soon..."
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return next_run.strftime("%H:%M"), f"{hours}h {minutes}m"


def _set_job(
    name: str,
    application: Application,
    enabled: bool | None = None,
    minutes: float | None = None,
    first: float | None = None,
) -> bool:
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
        enabled = any(
            current_mode(m) != MODE_OFF
            for m in ((cfg["monitor"],) if cfg.get("monitor") else MONITORS)
        )
    if not enabled:
        return False

    if cfg["kind"] == "loop":
        callback = cfg["callback"]

        async def keep_running() -> None:
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

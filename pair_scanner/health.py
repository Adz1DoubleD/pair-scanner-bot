import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import sentry_sdk

from .db.core import db_session
from .db.pairs import db_total_mb, pairs_get_by_status, pairs_get_stats
from .networks import NETWORKS
from .settings import (
    MODE_LIVE,
    MODE_NAMES,
    MODE_OFF,
    MONITORS,
    OPTIONS,
    SETTINGS,
    current_mode,
    forced_mode,
)
from .strategy import STRATEGY_RUN
from .w3 import get_w3_service
from .w3.tools import fetch_network_bal

PAIRS_BACKLOG_LIMIT = 10
STARTED_AT = datetime.now(timezone.utc)


async def scanner_health():
    trading_on = any(current_mode(m) == MODE_LIVE for m in MONITORS)
    alerts = []

    try:
        open_positions = len(
            await pairs_get_by_status(status="monitoring", table="positions") or []
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        open_positions = 0

    db_problem = True
    try:
        mb = await db_total_mb()
        async with db_session() as cur:
            await cur.execute("SHOW GRANTS FOR CURRENT_USER()")
            grants = " ".join(str(r) for r in await cur.fetchall())
        limit = float(OPTIONS.get("db_cleanup_mb") or 0)
        if "INSERT" not in grants and "ALL PRIVILEGES" not in grants:
            db_line = f"🔴 Read-only ({mb:.2f} MB)"
            alerts.append("🗄️ *Database*: read-only, nothing can be saved")
        elif limit and mb >= limit:
            db_line = f"🟡 {mb:.2f} MB"
            alerts.append(
                f"🗄️ *Database*: `{mb:.2f} MB`, still over the {limit:g} MB clean-up level"
            )
        else:
            db_line = f"🟢 {mb:.2f} MB"
            db_problem = False
    except Exception as e:
        sentry_sdk.capture_exception(e)
        db_line = "🔴 Offline"
        alerts.append("🔥 *Database*: connection failed")

    networks = sorted(NETWORKS.items())
    rpc_checks = await asyncio.gather(
        *(get_w3_service(conf) for _, conf in networks), return_exceptions=True
    )
    rpc_lines = []
    for (name, _), result in zip(networks, rpc_checks):
        if isinstance(result, BaseException):
            sentry_sdk.capture_exception(result)
            rpc_lines.append(f"{name}: 🔴 Check failed")
            alerts.append(f"🔥 *{name} RPC*: check failed")
            continue
        lines, down = result
        up, total = len(lines) - len(down), len(lines)
        if not down:
            rpc_lines.append(f"{name}: 🟢 {up}/{total} up")
        elif len(down) == total:
            rpc_lines.append(f"{name}: 🔴 all down")
            alerts.append(f"🔥 *{name} RPC*: all down")
        else:
            names = ", ".join(d.replace("❌ ", "") for d in down)
            rpc_lines.append(f"{name}: 🟡 {up}/{total} up ({names})")

    total_eth = 0.0
    failed = []
    balances = await asyncio.gather(
        *(
            fetch_network_bal(name, conf, SETTINGS.wallet_address)
            for name, conf in NETWORKS.items()
        ),
        return_exceptions=True,
    )
    for name, res in zip(NETWORKS, balances):
        eth = None if isinstance(res, BaseException) else res[1]
        if eth is None:
            failed.append(name)
        else:
            total_eth += eth

    minimum = float(OPTIONS.get("min_eth_balance") or 0)
    eth_low = not failed and total_eth < minimum
    if trading_on and eth_low:
        alerts.append(f"⚠️ *LOW ETH BALANCE*: `{total_eth:.4f} ETH`")
    elif trading_on and failed and total_eth < minimum:
        alerts.append(f"⚠️ *ETH BALANCE UNKNOWN*: check failed for {', '.join(failed)}")

    if current_mode("pairs") != MODE_OFF:
        try:
            stats, _, _, last_scan = await pairs_get_stats(days=1)
        except Exception as e:
            sentry_sdk.capture_exception(e)
        else:
            if stats.get("awaiting", 0) > PAIRS_BACKLOG_LIMIT:
                alerts.append(
                    "📦 *PAIRS BACKLOG*: Discovery outstripping processing capacity."
                )
            interval = timedelta(
                minutes=float(OPTIONS.get("health_check_interval") or 60)
            )
            found = (last_scan or {}).get("found_at")
            if found is not None and found.tzinfo is None:
                found = found.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - max(found or STARTED_AT, STARTED_AT)
            if age >= interval and found is None:
                alerts.append("📉 *LOW PAIRS PULSE*: no new pairs found")
            elif age >= interval:
                hours, minutes = divmod(int(age.total_seconds() // 60), 60)
                alerts.append(
                    f"📉 *LOW PAIRS PULSE*: no new pairs for {hours}h {minutes}m"
                )

    if current_mode("strategy") != MODE_OFF:
        every = timedelta(minutes=float(OPTIONS.get("strategy_monitor_interval") or 60))
        if STRATEGY_RUN.get("error"):
            alerts.append(f"🧭 *STRATEGY*: pool list failed ({STRATEGY_RUN['error']})")
        elif STRATEGY_RUN and not STRATEGY_RUN.get("fetched"):
            alerts.append("🧭 *STRATEGY*: the pool list came back empty")
        elif (
            datetime.now(timezone.utc) - STRATEGY_RUN.get("at", STARTED_AT) >= 2 * every
        ):
            alerts.append("🧭 *STRATEGY*: hasn't run on schedule")

    monitors = (
        [
            f"Pairs: {MODE_NAMES[current_mode('pairs')]}",
            f"Strategy: {MODE_NAMES[current_mode('strategy')]}"
            + (
                f" · last run {STRATEGY_RUN['at'].astimezone(ZoneInfo(SETTINGS.timezone)):%H:%M}, "
                f"{STRATEGY_RUN['fetched']} pools"
                if STRATEGY_RUN
                else ""
            ),
        ]
        + (
            [f"Capped at {MODE_NAMES[forced_mode()]} for this run"]
            if forced_mode() is not None
            else []
        )
        + [
            f"Open positions: {open_positions}",
        ]
    )
    wallet = [
        f"ETH: {total_eth:.4f}"
        if not failed
        else f"ETH: check failed for {', '.join(failed)}"
    ]
    sections = (
        ("🌐 *Web3*", rpc_lines),
        ("💰 *Wallet*", wallet),
        ("🗄️ *Database*", [db_line]),
        ("🛰️ *Token Monitors*", monitors),
    )
    text = "\n\n".join(
        title
        + "\n"
        + "\n".join(
            f" {'└' if i == len(lines) - 1 else '├'} {line}"
            for i, line in enumerate(lines)
        )
        for title, lines in sections
    )
    return {
        "text": text,
        "alerts": alerts,
        "low_eth": eth_low and trading_on,
        "open_positions": open_positions,
        "eth": None if failed else total_eth,
        "db_problem": db_problem,
    }

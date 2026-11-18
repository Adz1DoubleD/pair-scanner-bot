import asyncio
from datetime import datetime, timedelta, timezone

import sentry_sdk

from .db.core import db_session
from .db.pairs import db_total_mb, pairs_get_stats
from .networks import NETWORKS
from .settings import OPTIONS, SETTINGS
from .w3 import get_w3_service
from .w3.tools import fetch_network_bal

PAIRS_BACKLOG_LIMIT = 10


async def scanner_health():
    pairs_on = int(OPTIONS.get("pairs_enabled", 0) or 0) == 1
    strategy_on = int(OPTIONS.get("strategy_enabled", 0) or 0) == 1
    trading_on = int(OPTIONS.get("trading_enabled", 0) or 0) == 1
    alerts = []

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
    if eth_low:
        alerts.append(f"⚠️ *LOW ETH BALANCE*: `{total_eth:.4f} ETH`")
    elif failed and total_eth < minimum:
        alerts.append(f"⚠️ *ETH BALANCE UNKNOWN*: check failed for {', '.join(failed)}")

    if pairs_on:
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
            if found is None:
                alerts.append("📉 *LOW PAIRS PULSE*: no new pairs found")
            else:
                if found.tzinfo is None:
                    found = found.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - found
                if age >= interval:
                    hours, minutes = divmod(int(age.total_seconds() // 60), 60)
                    alerts.append(
                        f"📉 *LOW PAIRS PULSE*: no new pairs for {hours}h {minutes}m"
                    )

    monitors = [
        f"Pairs: {'🟢 ON' if pairs_on else '🔴 OFF'}",
        f"Strategy: {'🟢 ON' if strategy_on else '🔴 OFF'}",
        f"Trading: {'🟢 ON' if trading_on else '👀 Watch-only'}",
    ]
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
        "low_eth": eth_low and trading_on and (pairs_on or strategy_on),
        "eth": None if failed else total_eth,
        "db_problem": db_problem,
    }

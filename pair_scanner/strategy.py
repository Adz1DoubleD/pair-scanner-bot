from datetime import datetime, timedelta, timezone

import sentry_sdk
from telegram.ext import ContextTypes

from .constants import PAIR_LABELS
from .db.pairs import pairs_get_by_status, pairs_position_add, pairs_strat_add
from .helpers import escape_markdown
from .networks import NETWORKS
from .settings import MODE_LIVE, OPTIONS, current_mode
from .w3.audit import (
    check_honeypot,
    fetch_candidates,
    get_security_audit,
    get_token_metrics,
)
from .w3.tools import dex_name, fetch_eth_usd_price, trade_result
from .w3.trade import execute_buy, simulate_sell

STRATEGY_RUN = {}


async def run_strategy_monitor(context: ContextTypes.DEFAULT_TYPE):
    s = OPTIONS
    conf = NETWORKS["Base"]

    min_liq_usd = float(s.get("strat_min_liq_usd", 250_000))
    min_momentum = float(s.get("strat_min_momentum_h1", 3.0))
    max_positions = int(s.get("max_positions", 5))
    buy_amount = float(s.get("buy_amount_eth", 0.0001))
    max_holder = float(s.get("max_top_holder_pct", 10))

    positions = [
        p
        for p in (await pairs_get_by_status(status=None, table="positions")) or []
        if isinstance(p, dict)
    ]
    held = [p for p in positions if p.get("status") == "monitoring"]
    traded_addrs = {(p.get("target_address") or "").lower() for p in positions}
    if current_mode("strategy") != MODE_LIVE:
        traded_addrs |= {
            (p.get("target_address") or "").lower()
            for p in await pairs_get_by_status(status="watched", table="pairs") or []
            if isinstance(p, dict)
        }

    candidates, fetch_error = await fetch_candidates()
    now = datetime.now(timezone.utc)
    STRATEGY_RUN.update(at=now, fetched=len(candidates), error=fetch_error)
    min_age = timedelta(hours=float(s.get("strat_min_pool_age_hours", 24) or 0))

    picks = sorted(
        (
            c
            for c in candidates
            if c["liq_usd"] >= min_liq_usd
            and c["change_h1"] >= min_momentum
            and c["token_addr"].lower() not in traded_addrs
            and (
                not min_age
                or (
                    c.get("created_at")
                    and now - datetime.fromisoformat(c["created_at"]) >= min_age
                )
            )
        ),
        key=lambda c: c["change_h1"],
        reverse=True,
    )

    scan = [
        "🧭 *Strategy Monitor*\n",
        f"🔎 Fetched: `{len(candidates)}` pools",
        f"🎯 Found: `{len(picks)}`",
    ]
    if fetch_error:
        scan.append(f"⚠️ Error: `{fetch_error}`")

    slots = max_positions - len(held)
    if slots <= 0:
        return {
            "text": "\n".join(
                scan + ["", f"⏸️ At max positions ({len(held)}/{max_positions})"]
            ),
            "charts": [],
        }

    if not picks:
        return {"text": "\n".join(scan + ["", "🚫 Nothing to buy"]), "charts": []}

    eth_usd = await fetch_eth_usd_price()
    passed = 0
    bought = 0
    results = []
    charts = []
    buys = []
    watched = []
    failed = []
    for c in picks:
        if bought >= slots:
            break
        token_addr = c["token_addr"]
        symbol = escape_markdown(c["symbol"])
        try:
            pool = await get_token_metrics(
                {"pair_address": c["pair_addr"], "target_address": token_addr}, conf
            )
            onchain_usd = float(pool.get("liq_eth") or 0) * 2 * eth_usd
            sec_status, is_renounced, gp_data = await get_security_audit(
                token_addr, conf["chain_id"]
            )
            is_safe = await check_honeypot(token_addr, conf["chain_id"])
            sellable = await simulate_sell(conf, token_addr, c["pair_addr"])
            tax_pct = float((gp_data or {}).get("sell_tax") or 0) * 100

            if not pool.get("success"):
                status, detail = "metrics-rejected", "pool can't be read"
            elif onchain_usd < min_liq_usd:
                status, detail = "low-liquidity", f"${onchain_usd:,.0f} in the pool"
            elif sellable is not True:
                status, detail = "honey-pot", "can't sell"
            elif is_safe is not True:
                status, detail = "honey-pot", "honeypot.is"
            elif gp_data is None:
                status, detail = "no-data", ""
            elif sec_status != "clear":
                status, detail = sec_status, ""
            elif tax_pct > 10:
                status, detail = "high-tax", f"{tax_pct:.0f}% sell tax"
            elif gp_data.get("top_holder_pct", 0) > max_holder:
                status = "whale"
                detail = f"{gp_data['top_holder_pct']:.0f}% in one wallet"
            else:
                status, detail = None, ""

            if status:
                await pairs_strat_add(token_addr, c["pair_addr"], c["symbol"], status)
                label = PAIR_LABELS.get(status, status)
                text = f"{label} ({detail})" if detail else label
                results.append(f"🚫 {symbol} — {escape_markdown(text)}")
                continue

            passed += 1
            if current_mode("strategy") != MODE_LIVE:
                await pairs_strat_add(
                    token_addr, c["pair_addr"], c["symbol"], "watched"
                )
                await pairs_position_add(
                    {
                        "target_address": token_addr,
                        "pair_address": c["pair_addr"],
                        "symbol": c["symbol"],
                        "network": "Base",
                        "eth_spent": buy_amount,
                        "status": "paper",
                    },
                    pool,
                    sec_status,
                    is_renounced,
                    gp_data,
                )
                watched.append(
                    f"${symbol} · {c['change_h1']:+.1f}% 1h · ${c['liq_usd']:,.0f}"
                )
                results.append(f"👀 {watched[-1]} · would buy")
                continue
            tx_hash, buy_error = await execute_buy(
                conf, token_addr, buy_amount, c["pair_addr"], monitor="strategy"
            )
            if not tx_hash:
                failed.append(f"❌ {symbol} — {escape_markdown(buy_error)}")
                results.append(failed[-1])
                continue

            entry = {
                "target_address": token_addr,
                "pair_address": c["pair_addr"],
                "symbol": c["symbol"],
                "network": "Base",
                "buy_tx": tx_hash,
                "eth_spent": ((await trade_result(conf, tx_hash)) or {}).get(
                    "eth_spent"
                ),
            }
            metrics = await get_token_metrics(entry, conf)
            await pairs_position_add(entry, metrics, sec_status, is_renounced, gp_data)
            await pairs_strat_add(token_addr, c["pair_addr"], c["symbol"], "monitoring")
            bought += 1
            charts.append((c["symbol"], c["pair_addr"]))
            liq = c["liq_usd"]
            liq_short = f"${liq / 1e6:.1f}M" if liq >= 1e6 else f"${liq / 1e3:.0f}k"
            dex = await dex_name(conf, c["pair_addr"], short=True)
            buys.append(
                f"${symbol} · {c['change_h1']:+.1f}% 1h · {liq_short}"
                + (f" · {dex}" if dex else "")
            )
            results.append(
                f"✅ ${symbol} ({c['change_h1']:+.1f}% / ${c['liq_usd']:,.0f})"
            )

        except Exception as e:
            sentry_sdk.capture_exception(e)
            failed.append(f"⚠️ {symbol} — {escape_markdown(e)}")
            results.append(failed[-1])

    scan += [
        f"🛡️ Passed: `{passed}`",
        f"🛒 Bought: `{bought}` ({len(held) + bought}/{max_positions} positions)",
    ]
    return {
        "text": "\n".join(scan + [""] + results),
        "charts": charts,
        "buys": buys,
        "watched": watched,
        "failed": failed,
        "positions": (len(held) + bought, max_positions),
    }

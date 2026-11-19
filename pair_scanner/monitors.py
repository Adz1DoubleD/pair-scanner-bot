import asyncio
from datetime import datetime, timezone

import sentry_sdk
from telegram.ext import ContextTypes

from .constants import PAIR_LABELS
from .db.pairs import (
    pairs_get_by_status,
    pairs_get_for_audit,
    pairs_get_status_count,
    pairs_mark_completed,
    pairs_position_add,
    pairs_position_update,
    pairs_strat_add,
)
from .helpers import escape_markdown
from .networks import (
    NETWORKS,
)
from .settings import MODE_LIVE, OPTIONS, current_mode
from .w3.audit import (
    audit_pair,
    check_honeypot,
    fetch_candidates,
    get_security_audit,
    get_token_metrics,
)
from .w3.tools import (
    dex_name,
    trade_result,
)
from .w3.trade import execute_buy, execute_sell, simulate_sell

_NO_ENTRY_WARNED = set()


async def run_pairs_monitor(context: ContextTypes.DEFAULT_TYPE):
    max_positions = int(OPTIONS.get("max_positions", 5))
    active_count = await pairs_get_status_count("monitoring", "positions")
    slots = max_positions - active_count
    if slots <= 0:
        return f"⏸️ Max positions reached ({active_count}/{max_positions})"

    ready_to_audit = await pairs_get_for_audit()
    if not ready_to_audit:
        return "📭 No pairs ready to audit"

    result = await audit_pair(context, ready_to_audit, max_buys=slots)
    if isinstance(result, dict):
        result["positions"] = (
            active_count + len(result["charts"]),
            max_positions,
        )
    return result


async def run_sell_monitor(context: ContextTypes.DEFAULT_TYPE):
    active_positions = await pairs_get_by_status(status="monitoring", table="positions")
    s = OPTIONS
    sells = []
    losses = []
    charts = []
    held = len([p for p in active_positions or [] if isinstance(p, dict)])
    max_positions = int(s.get("max_positions", 5))

    if not active_positions:
        return {"sells": sells, "losses": losses, "charts": charts}

    for pos in active_positions:
        if not isinstance(pos, dict):
            continue

        pid = pos.get("id")
        token_addr = pos.get("target_address")
        symbol = escape_markdown(pos.get("symbol") or "???")
        tracked_at = pos.get("tracked_at")
        has_tx = bool(pos.get("buy_tx"))

        try:
            network_key = pos.get("network", "Base")
            network_conf = NETWORKS.get(network_key)

            new_metrics = await get_token_metrics(pos, network_conf)

            try:
                new_price = float(new_metrics.get("price_eth", 0))
                new_liq = float(new_metrics.get("liq_eth", 0))
                entry_price = float(pos.get("entry_price_eth", 0))
                entry_liq = float(pos.get("entry_liq_eth", 0))

                if entry_price <= 0 or entry_liq <= 0:
                    raise ValueError("entry price or liquidity is zero")
            except (TypeError, ValueError) as e:
                if pid not in _NO_ENTRY_WARNED:
                    _NO_ENTRY_WARNED.add(pid)
                    sentry_sdk.capture_exception(e)
                    sells.append(
                        f"⚠️ ${symbol} · no entry price, not monitored — "
                        "sell or remove it"
                    )
                continue

            pnl = round(((new_price - entry_price) / entry_price * 100), 2)
            liq_change = round(((new_liq - entry_liq) / entry_liq * 100), 2)

            tp_trigger = float(s.get("take_profit_percent", 50))
            sl_trigger = float(s.get("stop_loss_percent", 20))
            rug_trigger = float(s.get("rug_liq_threshold", 30))
            trailing_sl = float(s.get("trailing_stop_percent", 20))
            trailing_start = float(s.get("trailing_start_percent", 20))
            peak_pnl = float(pos.get("peak_pnl_percent") or 0)
            dead_hours = float(s.get("dead_token_hours", 1))
            dead_threshold = float(s.get("dead_token_threshold", 2.0))
            half_at = float(s.get("half_sell_percent", 0) or 0)
            half_eth = pos.get("half_eth")
            written_off = (
                round((float(half_eth) / float(pos["eth_spent"]) - 1) * 100, 2)
                if half_eth is not None and pos.get("eth_spent")
                else -100.0
            )

            should_sell = False
            is_rug = False
            age_hours = 0

            if tracked_at:
                if not tracked_at.tzinfo:
                    tracked_at = tracked_at.replace(tzinfo=timezone.utc)
                age_hours = (
                    datetime.now(timezone.utc) - tracked_at
                ).total_seconds() / 3600

            sec_status = pos.get("current_sec_status")
            is_renounced = pos.get("is_renounced")
            gp_data = None
            is_safe = True

            try:
                sec_status, is_renounced, gp_data = await get_security_audit(
                    token_addr, network_conf["chain_id"]
                )
                is_safe = await check_honeypot(token_addr, network_conf["chain_id"])
            except Exception as sec_e:
                sentry_sdk.capture_exception(sec_e)

            is_honeypot_detected = (
                is_safe is False or (gp_data and gp_data.get("is_honeypot"))
            ) and pos.get("entry_sec_status") != "honey-pot"

            if is_honeypot_detected:
                await pairs_position_update(
                    new_metrics, pnl, liq_change, sec_status, is_renounced, pid, gp_data
                )
                tx_hash, error = (
                    await execute_sell(network_conf, pos) if has_tx else (None, None)
                )
                if error == "PENDING":
                    continue
                held -= 1
                if tx_hash:
                    result = await trade_result(
                        network_conf, pos["buy_tx"], tx_hash, earlier_eth=half_eth
                    )
                    await pairs_mark_completed(
                        token_addr, sell_tx=tx_hash, **(result or {})
                    )
                    real_pnl = result["pnl"] if result else pnl
                    (losses if real_pnl < 0 else sells).append(
                        f"☠️ ${symbol} · honeypot flagged, sold · {real_pnl:+.2f}%"
                    )
                elif error == "No token balance":
                    await pairs_mark_completed(token_addr, pnl=pnl)
                    sells.append(f"📝 ${symbol} · no tokens left, closed · {pnl:+.2f}%")
                else:
                    await pairs_mark_completed(token_addr, pnl=written_off)
                    losses.append(
                        f"☠️ ${symbol} · honeypot detected · {written_off:+g}%"
                    )
                continue

            is_dead = (
                age_hours >= dead_hours
                and abs(pnl) < dead_threshold
                and peak_pnl < dead_threshold
                and abs(liq_change) < dead_threshold
            )

            reason = None
            if is_dead:
                reason = "dead token"
            elif liq_change <= -rug_trigger:
                is_rug = True
                reason = "rug"
            elif pnl >= tp_trigger:
                reason = "take-profit"
            elif pnl <= -sl_trigger:
                reason = "stop-loss"
            elif half_eth is not None and pnl <= 0:
                reason = "break-even"
            elif peak_pnl >= trailing_start and (peak_pnl - pnl) >= trailing_sl:
                reason = "trailing-stop"
            should_sell = reason is not None

            if (
                not should_sell
                and has_tx
                and half_eth is None
                and half_at > 0
                and pnl >= half_at
            ):
                tx_hash, error = await execute_sell(network_conf, pos, half=True)
                if tx_hash:
                    result = await trade_result(network_conf, pos["buy_tx"], tx_hash)
                    await pairs_position_update(
                        new_metrics,
                        pnl,
                        liq_change,
                        sec_status,
                        is_renounced,
                        pid,
                        gp_data,
                        half_eth=result["eth_received"] if result else 0,
                        eth_spent=result["eth_spent"] if result else None,
                    )
                    if pos.get("pair_address"):
                        charts.append((pos.get("symbol") or "???", pos["pair_address"]))
                    sells.append(f"${symbol} · sold half · {pnl:+.2f}%")
                    continue

            if should_sell:
                if not has_tx:
                    await pairs_position_update(
                        new_metrics,
                        pnl,
                        liq_change,
                        sec_status,
                        is_renounced,
                        pid,
                        gp_data,
                    )
                    await pairs_mark_completed(token_addr, pnl=0)
                    held -= 1
                    sells.append(f"📝 ${symbol} · {reason}, no buy tx · 0%")
                    continue

                tx_hash, error = await execute_sell(network_conf, pos)

                if error and "TRANSFER_FROM_FAILED" in error:
                    await pairs_position_update(
                        new_metrics,
                        pnl,
                        liq_change,
                        sec_status,
                        is_renounced,
                        pid,
                        gp_data,
                    )
                    await pairs_mark_completed(token_addr, pnl=written_off)
                    held -= 1
                    losses.append(f"💀 ${symbol} · unsellable · {written_off:+g}%")
                    continue

                if error == "No token balance":
                    await pairs_position_update(
                        new_metrics,
                        pnl,
                        liq_change,
                        sec_status,
                        is_renounced,
                        pid,
                        gp_data,
                    )
                    await pairs_mark_completed(token_addr, pnl=pnl)
                    held -= 1
                    sells.append(f"📝 ${symbol} · no tokens left, closed · {pnl:+.2f}%")
                    continue

                if tx_hash:
                    await pairs_position_update(
                        new_metrics,
                        pnl,
                        liq_change,
                        sec_status,
                        is_renounced,
                        pid,
                        gp_data,
                    )
                    result = await trade_result(
                        network_conf, pos["buy_tx"], tx_hash, earlier_eth=half_eth
                    )
                    await pairs_mark_completed(
                        token_addr, sell_tx=tx_hash, **(result or {})
                    )
                    real_pnl = result["pnl"] if result else pnl
                    held -= 1
                    if pos.get("pair_address"):
                        charts.append((pos.get("symbol") or "???", pos["pair_address"]))
                    dex = (
                        await dex_name(network_conf, pos["pair_address"], short=True)
                        if pos.get("pair_address")
                        else None
                    )
                    (losses if real_pnl < 0 else sells).append(
                        f"${symbol} · {reason} · {real_pnl:+.2f}%"
                        + (f" · {dex}" if dex else "")
                    )
                    continue
                else:
                    if is_rug and error != "PENDING":
                        await pairs_position_update(
                            new_metrics,
                            pnl,
                            liq_change,
                            sec_status,
                            is_renounced,
                            pid,
                            gp_data,
                        )
                        await pairs_mark_completed(token_addr, pnl=written_off)
                        held -= 1
                        losses.append(
                            f"🚨 ${symbol} · rug, sell failed · {written_off:+g}%"
                        )
                        continue

            await pairs_position_update(
                new_metrics, pnl, liq_change, sec_status, is_renounced, pid, gp_data
            )

            await asyncio.sleep(0.5)

        except Exception as e:
            sentry_sdk.capture_exception(e)

    return {
        "sells": sells,
        "losses": losses,
        "charts": charts,
        "positions": (held, max_positions),
    }


async def run_sell_all(context: ContextTypes.DEFAULT_TYPE):
    rows = await pairs_get_by_status(status=None, table="positions") or []
    seen = set()
    sold, skipped, failed = [], [], []

    for pos in rows:
        if not isinstance(pos, dict):
            continue
        addr = (pos.get("target_address") or "").lower()
        conf = NETWORKS.get(pos.get("network") or "Base")
        if not addr or addr in seen or not conf:
            continue
        seen.add(addr)
        symbol = escape_markdown(pos.get("symbol") or "???")

        try:
            tx_hash, error = await execute_sell(conf, pos, skip_dust=True)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            failed.append(f"❌ ${symbol} — {escape_markdown(e)}")
            continue

        if tx_hash:
            if pos.get("status") == "monitoring":
                result = await trade_result(
                    conf, pos.get("buy_tx"), tx_hash, earlier_eth=pos.get("half_eth")
                )
                await pairs_mark_completed(
                    pos["target_address"], sell_tx=tx_hash, **(result or {})
                )
            sold.append(f"✅ ${symbol} sold")
        elif error == "dust":
            skipped.append(f"🧹 ${symbol} — worth less than the gas")
        elif error == "PENDING":
            skipped.append(f"⏳ ${symbol} — sell sent, not confirmed yet")
        elif error != "No token balance":
            failed.append(f"❌ ${symbol} — {escape_markdown(error)}")

    lines = sold + skipped + failed
    if not lines:
        return "💼 *Sell All*\n\nNothing left in the wallet to sell."
    return "💼 *Sell All*\n\n" + "\n".join(lines)


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

    picks = sorted(
        (
            c
            for c in candidates
            if c["liq_usd"] >= min_liq_usd
            and c["change_h1"] >= min_momentum
            and c["token_addr"].lower() not in traded_addrs
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
            sec_status, is_renounced, gp_data = await get_security_audit(
                token_addr, conf["chain_id"]
            )
            is_safe = await check_honeypot(token_addr, conf["chain_id"])
            sellable = await simulate_sell(conf, token_addr, c["pair_addr"])
            tax_pct = float((gp_data or {}).get("sell_tax") or 0) * 100

            if sellable is not True:
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

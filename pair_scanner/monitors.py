import asyncio
from datetime import datetime, timezone

import sentry_sdk
from config import BOT_SETTINGS
from config.scanner import PAIR_LABELS
from config.w3 import (
    AERO_POOL_CREATED_TOPIC,
    ERC20_ABI,
    NETWORKS,
    PAIR_CREATED_TOPIC,
    POOL_CREATED_TOPIC,
)
from services.db.pairs import (
    pairs_add,
    pairs_get_by_status,
    pairs_get_for_audit,
    pairs_get_status_count,
    pairs_mark_as_invalid,
    pairs_mark_completed,
    pairs_position_add,
    pairs_position_update,
    pairs_strat_add,
    pairs_update_metadata,
)
from services.w3 import (
    open_w3,
)
from services.w3.audit import (
    audit_pair,
    check_honeypot,
    fetch_candidates,
    get_security_audit,
    get_token_metrics,
    parse_pair_created,
)
from services.w3.tools import (
    dex_name,
    trade_result,
)
from services.w3.trade import execute_buy, execute_sell, simulate_sell
from telegram.ext import ContextTypes
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from ..utils import escape_markdown

_NO_ENTRY_WARNED = set()


async def run_pairs_monitor(context: ContextTypes.DEFAULT_TYPE):
    max_positions = int(BOT_SETTINGS.get("max_positions", 5))
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
    s = BOT_SETTINGS
    sells = []
    charts = []
    held = len([p for p in active_positions or [] if isinstance(p, dict)])
    max_positions = int(s.get("max_positions", 5))

    if not active_positions:
        return {"sells": sells, "charts": charts}

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
            peak_pnl = float(pos.get("peak_pnl_percent") or 0)
            dead_hours = float(s.get("dead_token_hours", 1))
            dead_threshold = float(s.get("dead_token_threshold", 2.0))

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
                    result = await trade_result(network_conf, pos["buy_tx"], tx_hash)
                    await pairs_mark_completed(
                        token_addr, sell_tx=tx_hash, **(result or {})
                    )
                    real_pnl = result["pnl"] if result else pnl
                    sells.append(
                        f"☠️ ${symbol} · honeypot flagged, sold · {real_pnl:+.2f}%"
                    )
                elif error == "No token balance":
                    await pairs_mark_completed(token_addr, pnl=pnl)
                    sells.append(f"📝 ${symbol} · no tokens left, closed · {pnl:+.2f}%")
                else:
                    await pairs_mark_completed(token_addr, pnl=-100)
                    sells.append(f"☠️ ${symbol} · honeypot detected · -100%")
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
            elif peak_pnl >= tp_trigger and (peak_pnl - pnl) >= trailing_sl:
                reason = "trailing-stop"
            should_sell = reason is not None

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
                    await pairs_mark_completed(token_addr, pnl=-100)
                    held -= 1
                    sells.append(f"💀 ${symbol} · unsellable · -100%")
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
                    result = await trade_result(network_conf, pos["buy_tx"], tx_hash)
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
                    sells.append(
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
                        await pairs_mark_completed(token_addr, pnl=-100)
                        held -= 1
                        sells.append(f"🚨 ${symbol} · rug, sell failed · -100%")
                        continue

            await pairs_position_update(
                new_metrics, pnl, liq_change, sec_status, is_renounced, pid, gp_data
            )

            await asyncio.sleep(0.5)

        except Exception as e:
            sentry_sdk.capture_exception(e)

    return {"sells": sells, "charts": charts, "positions": (held, max_positions)}


async def run_sell_all(context):
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
                result = await trade_result(conf, pos.get("buy_tx"), tx_hash)
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


async def run_strategy_monitor(context):
    s = BOT_SETTINGS
    conf = NETWORKS["Base"]

    min_liq_usd = float(s.get("strat_min_liq_usd", 250_000))
    min_momentum = float(s.get("strat_min_momentum_h1", 3.0))
    max_positions = int(s.get("max_positions", 5))
    buy_amount = float(s.get("buy_amount_eth", 0.0001))

    positions = [
        p
        for p in (await pairs_get_by_status(status=None, table="positions")) or []
        if isinstance(p, dict)
    ]
    held = [p for p in positions if p.get("status") == "monitoring"]
    traded_addrs = {(p.get("target_address") or "").lower() for p in positions}

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
            else:
                status, detail = None, ""

            if status:
                await pairs_strat_add(token_addr, c["pair_addr"], c["symbol"], status)
                label = PAIR_LABELS.get(status, status)
                text = f"{label} ({detail})" if detail else label
                results.append(f"🚫 {symbol} — {escape_markdown(text)}")
                continue

            passed += 1
            tx_hash, buy_error = await execute_buy(
                conf, token_addr, buy_amount, c["pair_addr"]
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
        "failed": failed,
        "positions": (len(held) + bought, max_positions),
    }


async def watch_new_pairs():
    last_block = None
    network_name = "Base"
    conf = NETWORKS[network_name]
    BUFFER = 2
    MAX_BLOCK_RANGE = 1000
    factories = [
        f
        for f in (
            conf["v2_factory"],
            conf["v3_factory"],
            conf.get("aero_factory"),
        )
        if f
    ]
    topics = [PAIR_CREATED_TOPIC, POOL_CREATED_TOPIC, AERO_POOL_CREATED_TOPIC]

    while True:
        try:
            w3 = await open_w3(conf)
            blockchain_tip = await w3.eth.block_number
            current_block = blockchain_tip - BUFFER

            if last_block is None:
                last_block = current_block - 1

            if current_block > last_block:
                from_block = last_block + 1

                while from_block <= current_block:
                    to_block = min(from_block + MAX_BLOCK_RANGE - 1, current_block)
                    logs = await w3.eth.get_logs(
                        {
                            "fromBlock": from_block,
                            "toBlock": to_block,
                            "address": factories,
                            "topics": [topics],
                        }
                    )

                    if logs:
                        for log in logs:
                            t0, t1, pair = parse_pair_created(log)

                            weth = conf["weth"].lower()
                            if t0.lower() == weth or t1.lower() == weth:
                                target = t1 if t0.lower() == weth else t0
                                await pairs_add(target, pair, network_name)

                    from_block = to_block + 1

                last_block = current_block

        except Exception as e:
            is_timeout = (
                isinstance(e, (asyncio.TimeoutError, TimeoutError))
                or "timeout" in str(e).lower()
            )
            is_rpc_sync = "-32602" in str(e) or "block range" in str(e).lower()
            is_server_error = (
                "status=500" in str(e) or "internal server error" in str(e).lower()
            )
            is_bad_request = "Bad Request" in str(e)

            if not (is_timeout or is_rpc_sync or is_server_error or is_bad_request):
                sentry_sdk.capture_exception(e)

            await asyncio.sleep(10)
            continue

        await asyncio.sleep(2)


async def watch_pair_metadata():
    failures = {}
    while True:
        pending = await pairs_get_by_status(status="pending", table="pairs", limit=5)

        if not pending:
            await asyncio.sleep(30)
            continue

        for row in pending:
            target_addr = row["target_address"]
            conf = NETWORKS.get(row["network"])
            w3 = await open_w3(conf)
            try:
                contract = w3.eth.contract(
                    address=Web3.to_checksum_address(target_addr), abi=ERC20_ABI
                )
                symbol = await contract.functions.symbol().call()
                decimals = await contract.functions.decimals().call()
            except (BadFunctionCallOutput, ContractLogicError, ValueError):
                await pairs_mark_as_invalid(target_addr)
                continue
            except Exception:
                failures[target_addr] = failures.get(target_addr, 0) + 1
                if failures[target_addr] >= 5:
                    failures.pop(target_addr)
                    await pairs_mark_as_invalid(target_addr)
                continue

            failures.pop(target_addr, None)
            await pairs_update_metadata(target_addr, symbol, decimals)

        await asyncio.sleep(5)

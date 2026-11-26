"""Checking tokens before buying: liquidity, security, honeypots and pool data."""

import asyncio
from decimal import Decimal, getcontext
from typing import Any

import httpx
import sentry_sdk
from telegram.ext import ContextTypes
from web3 import Web3

from ..constants import PAIR_LABELS
from ..db.pairs import (
    pairs_mark_audit_complete,
    pairs_mark_for_retry,
    pairs_position_add,
)
from ..helpers import GECKO_POOLS, escape_markdown, fetch, to_float
from ..networks import (
    ERC20_ABI,
    NETWORKS,
    POOL_CREATED_TOPIC,
    V3_POOL_ABI,
    WETH_ABI,
)
from ..settings import MODE_LIVE, MODE_NAMES, OPTIONS, current_mode
from . import open_w3, pool_kind
from .tools import dex_name, trade_result
from .trade import execute_buy, simulate_sell

getcontext().prec = 50


async def audit_pair(
    context: ContextTypes.DEFAULT_TYPE | None,
    entries: dict[str, Any] | list[dict[str, Any]],
    preview: bool = False,
    force_buy: bool = False,
    max_buys: int | None = None,
) -> Any:
    targets = [entries] if isinstance(entries, dict) else entries
    passed = 0
    bought = 0
    results = []
    charts = []
    buys = []
    watched = []
    failed = []

    for entry in targets:
        if max_buys is not None and bought >= max_buys:
            break

        symbol = escape_markdown(entry.get("symbol") or "???")
        try:
            token_addr = entry["target_address"]
            conf = NETWORKS[entry.get("network") or "Base"]

            min_liq_eth = Decimal(str(OPTIONS.get("min_liquidity_eth", 0.5)))
            min_liq = int(min_liq_eth * 10**18)
            min_ratio = float(OPTIONS.get("min_ratio_percent", 15.0))
            min_lp_locked = float(OPTIONS.get("min_lp_locked_pct", 70))
            max_holder = float(OPTIONS.get("max_top_holder_pct", 10))

            w3 = await open_w3(conf)
            pair = Web3.to_checksum_address(entry["pair_address"])
            weth_addr = Web3.to_checksum_address(conf["weth"])
            weth_contract = w3.eth.contract(address=weth_addr, abi=WETH_ABI)
            balance = await weth_contract.functions.balanceOf(pair).call()
            if balance < min_liq and not (preview or force_buy):
                await pairs_mark_audit_complete(token_addr, "low-liquidity")
                label = PAIR_LABELS["low-liquidity"]
                results.append(f"🚫 {symbol} — {escape_markdown(label)}")
                continue

            sec_status, is_renounced, gp_data = await get_security_audit(
                token_addr, conf["chain_id"]
            )
            is_safe, can_sell = await asyncio.gather(
                check_honeypot(token_addr, conf["chain_id"]),
                simulate_sell(conf, token_addr, entry["pair_address"]),
            )
            metrics = await get_token_metrics(entry, conf)
            m_ratio = float(metrics.get("ratio", 0)) if metrics.get("success") else 0
            lp_locked_pct = gp_data.get("lp_locked_pct", 0) if gp_data else 0
            _, fee = await pool_kind(w3, conf, pair)
            is_v3 = fee is not None

            reasons = []
            if balance < min_liq:
                reasons.append("low-liquidity")
            if gp_data is None:
                reasons.append("no-data")
            elif sec_status != "clear":
                reasons.append(sec_status)
            if can_sell is False or is_safe is not True:
                reasons.append("honey-pot")
            elif can_sell is None:
                reasons.append("sell-unchecked")
            if gp_data and not metrics.get("success"):
                reasons.append("metrics-rejected")
            elif gp_data and m_ratio < min_ratio:
                reasons.append("low-ratio")
            if gp_data and not is_v3 and lp_locked_pct < min_lp_locked:
                reasons.append("lp-unlocked")
            top_holder = gp_data.get("top_holder_pct", 0) if gp_data else 0
            if top_holder > max_holder:
                reasons.append("whale")

            if preview:
                verdict = "✅ WOULD PASS" if not reasons else "❌ WOULD REJECT"
                reason_line = (
                    ""
                    if not reasons
                    else "\n ├ Reasons: "
                    + ", ".join(PAIR_LABELS.get(x, x) for x in reasons)
                )
                buy_tax = gp_data.get("buy_tax") if gp_data else None
                sell_tax = gp_data.get("sell_tax") if gp_data else None
                buy_str = f"{buy_tax * 100:.0f}%" if buy_tax is not None else "Unknown"
                sell_str = (
                    f"{sell_tax * 100:.0f}%" if sell_tax is not None else "Unknown"
                )
                return (
                    f"💧 Liquidity: `{balance / 10**18:.4f} ETH` (min {float(min_liq_eth):.2f})\n"
                    f"📊 Ratio: `{m_ratio:.1f}%` (min {min_ratio:.0f}%)\n"
                    + (
                        "🔒 LP Locked: `n/a (Uniswap V3)`\n"
                        if is_v3
                        else f"🔒 LP Locked: `{lp_locked_pct:.0f}%` (min {min_lp_locked:.0f}%)\n"
                    )
                    + f"🐋 Top Holder: `{top_holder:.0f}%` (max {max_holder:.0f}%)\n"
                    f"🔐 Renounced: {'✅' if is_renounced else '❌'}\n"
                    f"🛡️ Security: `{sec_status if gp_data else 'no-data'}`\n"
                    f"💸 Tax: Buy `{buy_str}` / Sell `{sell_str}`\n"
                    f"🍯 Honeypot-safe: {'✅' if is_safe is True else '❌'}  |  "
                    f"Sellable: {'✅' if can_sell else '❌'}\n"
                    f"✔️ Verified: {'✅' if gp_data and gp_data.get('is_verified') else '❌'}\n\n"
                    f"🚦 *{verdict}*{reason_line}"
                )

            if reasons and not force_buy:
                await pairs_mark_audit_complete(token_addr, reasons[0])
                label = PAIR_LABELS.get(reasons[0], reasons[0])
                results.append(f"🚫 {symbol} — {escape_markdown(label)}")
                continue

            passed += 1
            if current_mode("pairs") != MODE_LIVE:
                if force_buy:
                    results.append(
                        f"👀 {symbol} — the Pairs Monitor is on "
                        f"{MODE_NAMES[current_mode('pairs')]}. Switch it to "
                        "💰 Live in ⚙️ Settings to buy."
                    )
                    continue
                await pairs_mark_audit_complete(token_addr, "watched")
                if metrics.get("success"):
                    await pairs_position_add(
                        {
                            **entry,
                            "eth_spent": OPTIONS.get("buy_amount_eth", 0.0001),
                            "status": "paper",
                        },
                        metrics,
                        sec_status,
                        is_renounced,
                        gp_data,
                    )
                watched.append(f"${symbol} · {balance / 10**18:.2f} ETH liquidity")
                results.append(f"👀 {watched[-1]} · would buy")
                continue
            buy_amount = OPTIONS.get("buy_amount_eth", 0.0001)
            tx_hash, buy_error = await execute_buy(
                conf, token_addr, buy_amount, entry["pair_address"], monitor="pairs"
            )
            if not tx_hash:
                entry["buy_error"] = buy_error
                await pairs_mark_for_retry(token_addr)
                failed.append(f"❌ {symbol} — {escape_markdown(buy_error)}")
                results.append(failed[-1])
                continue

            entry["buy_tx"] = tx_hash
            entry["eth_spent"] = ((await trade_result(conf, tx_hash)) or {}).get(
                "eth_spent"
            )
            await pairs_position_add(entry, metrics, sec_status, is_renounced, gp_data)
            await pairs_mark_audit_complete(token_addr, "monitoring")
            bought += 1
            charts.append((entry.get("symbol") or "???", entry["pair_address"]))
            forced = " · forced" if reasons else ""
            dex = await dex_name(conf, entry["pair_address"], short=True)
            dex = f" · {dex}" if dex else ""
            buys.append(
                f"${symbol} · {balance / 10**18:.2f} ETH liquidity{dex}{forced}"
            )
            results.append(f"✅ {buys[-1]}")

        except Exception as e:
            sentry_sdk.capture_exception(e)
            if preview:
                return f"⚠️ Preview failed: `{e}`"
            failed.append(f"⚠️ {symbol} — {escape_markdown(e)}")
            results.append(failed[-1])

    scan = [
        "🎯 *Pair Audit*\n",
        f"🔎 Audited: `{len(results)}`",
        f"🛡️ Passed: `{passed}`",
        f"🛒 Bought: `{bought}`",
    ]
    return {
        "text": "\n".join(scan + [""] + results),
        "charts": charts,
        "buys": buys,
        "watched": watched,
        "failed": failed,
    }


async def check_honeypot(token_addr: str, chain_id: int = 8453) -> bool | None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.honeypot.is/v2/IsHoneypot",
                params={"address": token_addr, "chainID": chain_id},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return None

    if (data.get("honeypotResult") or {}).get("isHoneypot"):
        return False
    level = (data.get("summary") or {}).get("riskLevel")
    if level is None:
        return None
    if level >= 90:
        return False
    return True if level < 20 else None


async def fetch_candidates(
    pages: int = 3,
) -> tuple[list[dict[str, Any]], str | None]:
    conf = NETWORKS["Base"]
    weth = conf["weth"].lower()
    base_url = GECKO_POOLS

    by_token: dict[str, dict[str, Any]] = {}
    error = None
    for dex in conf.get("gecko_dexes", ("uniswap-v2-base",)):
        url = (
            base_url.replace("/pools", f"/dexes/{dex}/pools")
            + "?sort=h24_volume_usd_desc&page="
        )
        for page in range(1, pages + 1):
            data = await fetch(f"{url}{page}")

            if not isinstance(data, dict) or data.get("error"):
                if page == 1:
                    status = data.get("status") if isinstance(data, dict) else "?"
                    error = f"fetch failed (status {status})"
                break

            pools = data.get("data", [])
            for pool in pools:
                attr = pool.get("attributes", {})
                rel = pool.get("relationships", {})

                base_ref = rel.get("base_token", {}).get("data", {}).get("id", "")
                quote_ref = rel.get("quote_token", {}).get("data", {}).get("id", "")
                token_addr = base_ref.split("_", 1)[-1] if "_" in base_ref else ""
                quote_addr = quote_ref.split("_", 1)[-1] if "_" in quote_ref else ""
                pair_addr = attr.get("address", "")
                if not token_addr.startswith("0x") or quote_addr.lower() != weth:
                    continue

                try:
                    liq_usd = float(attr.get("reserve_in_usd") or 0)
                    change_h1 = float(
                        (attr.get("price_change_percentage") or {}).get("h1") or 0
                    )
                except (TypeError, ValueError):
                    continue

                key = token_addr.lower()
                if key in by_token and by_token[key]["liq_usd"] >= liq_usd:
                    continue
                by_token[key] = {
                    "token_addr": token_addr,
                    "pair_addr": pair_addr,
                    "symbol": (attr.get("name") or "???").split(" / ")[0],
                    "liq_usd": liq_usd,
                    "change_h1": change_h1,
                    "created_at": attr.get("pool_created_at"),
                }

            if not pools:
                break

    if not by_token and error:
        return [], error
    return list(by_token.values()), None


async def get_security_audit(
    address: str, chain_id: int
) -> tuple[str, bool, dict[str, Any] | None]:
    url = f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses={address}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            data = response.json().get("result", {}).get(address.lower(), {})
            if not data:
                return "no-data", False, None

            def flag(key: str) -> bool:
                return data.get(key) == "1"

            is_verified = flag("is_open_source")
            buy_tax = to_float(data.get("buy_tax"))
            sell_tax = to_float(data.get("sell_tax"))

            owner_addr = (data.get("owner_address") or "").lower()
            is_renounced = owner_addr in (
                "0x0000000000000000000000000000000000000000",
                "0x000000000000000000000000000000000000dead",
                "",
            )

            danger = any(
                [
                    flag("is_honeypot"),
                    flag("cannot_sell_all"),
                    flag("cannot_buy"),
                    flag("transfer_pausable"),
                    flag("is_blacklisted"),
                    flag("is_whitelisted"),
                    flag("is_mintable"),
                    flag("selfdestruct"),
                    flag("hidden_owner"),
                    flag("owner_change_balance"),
                    flag("can_take_back_ownership"),
                    flag("slippage_modifiable"),
                    flag("personal_slippage_modifiable"),
                    flag("trading_cooldown"),
                    flag("is_proxy"),
                ]
            )

            dead = (
                "0x000000000000000000000000000000000000dead",
                "0x0000000000000000000000000000000000000000",
            )
            lp_locked_pct = 0.0
            for lp in data.get("lp_holders") or []:
                pct = to_float(lp.get("percent")) or 0.0
                locked = str(lp.get("is_locked")) == "1"
                burned = (lp.get("address") or "").lower() in dead
                if locked or burned:
                    lp_locked_pct += pct * 100

            top_holder_pct = 0.0
            for h in data.get("holders") or []:
                if (
                    str(h.get("is_locked")) == "1"
                    or str(h.get("is_contract")) == "1"
                    or (h.get("address") or "").lower() in dead
                ):
                    continue
                if (h.get("tag") or "") == "":
                    top_holder_pct = max(
                        top_holder_pct, (to_float(h.get("percent")) or 0.0) * 100
                    )

            if danger:
                status = "honey-pot"
            elif not is_verified:
                status = "unverified"
            elif sell_tax is None or buy_tax is None:
                status = "clear"
            elif sell_tax > 0.15 or buy_tax > 0.15:
                status = "high-tax"
            else:
                status = "clear"

            gp_data = {
                "buy_tax": buy_tax,
                "sell_tax": sell_tax,
                "is_honeypot": danger,
                "is_verified": is_verified,
                "is_renounced": is_renounced,
                "lp_locked_pct": lp_locked_pct,
                "top_holder_pct": top_holder_pct,
            }

            return status, is_renounced, gp_data

    except Exception as e:
        sentry_sdk.capture_exception(e)
        return "no-data", False, None


async def get_token_metrics(
    pair_data: dict[str, Any], network_conf: dict[str, Any]
) -> dict[str, Any]:
    stats = {
        "price_eth": "0.000000000000000000",
        "liq_eth": "0.000000000000000000",
        "mcap_eth": "0.000000000000000000",
        "ratio": 0.0,
        "supply": 0,
        "decimals": 18,
        "success": False,
    }

    try:
        w3 = await open_w3(network_conf)

        pair_addr = Web3.to_checksum_address(pair_data["pair_address"])
        token_addr = Web3.to_checksum_address(pair_data["target_address"])
        weth_addr = Web3.to_checksum_address(network_conf["weth"])

        weth_contract = w3.eth.contract(address=weth_addr, abi=WETH_ABI)
        token_contract = w3.eth.contract(address=token_addr, abi=ERC20_ABI)

        p_weth, p_token, supply, dec = await asyncio.gather(
            weth_contract.functions.balanceOf(pair_addr).call(),
            token_contract.functions.balanceOf(pair_addr).call(),
            token_contract.functions.totalSupply().call(),
            token_contract.functions.decimals().call(),
        )

        d_weth = Decimal(p_weth) / Decimal(10**18)
        d_token = Decimal(p_token) / Decimal(10**dec)
        d_supply = Decimal(supply) / Decimal(10**dec)

        price_eth = Decimal(0)
        _, fee = await pool_kind(w3, network_conf, pair_addr)
        if fee is None:
            if d_token > 0:
                price_eth = d_weth / d_token
        else:
            pool = w3.eth.contract(address=pair_addr, abi=V3_POOL_ABI)
            slot0, token0 = await asyncio.gather(
                pool.functions.slot0().call(), pool.functions.token0().call()
            )
            raw = Decimal(slot0[0]) ** 2 / Decimal(2**192)
            if raw > 0:
                if token0.lower() == token_addr.lower():
                    price_eth = raw * Decimal(10**dec) / Decimal(10**18)
                else:
                    price_eth = Decimal(10**dec) / (raw * Decimal(10**18))

        if price_eth > 0 and d_weth > 0:
            mcap_eth = price_eth * d_supply
            ratio = ((d_weth * 2) / mcap_eth) * 100

            stats.update(
                {
                    "price_eth": f"{price_eth:.18f}",
                    "liq_eth": f"{d_weth:.18f}",
                    "mcap_eth": f"{mcap_eth:.18f}",
                    "ratio": float(ratio),
                    "supply": float(d_supply),
                    "decimals": dec,
                    "success": True,
                }
            )

    except Exception as e:
        sentry_sdk.capture_exception(e)

    return stats


def parse_pair_created(log: Any) -> tuple[str, str, str]:
    t0 = "0x" + log["topics"][1].hex()[-40:]
    t1 = "0x" + log["topics"][2].hex()[-40:]
    topic = "0x" + log["topics"][0].hex().removeprefix("0x")
    start = 44 if topic.lower() == POOL_CREATED_TOPIC else 12
    pair = "0x" + log["data"][start : start + 20].hex().removeprefix("0x")
    return t0, t1, pair

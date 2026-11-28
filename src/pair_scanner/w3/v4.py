"""Checking new Uniswap v4 pools. Watch-only: picks become pretend positions."""

import unicodedata
from decimal import Decimal
from typing import Any

import sentry_sdk
from web3 import Web3
from web3.exceptions import ContractLogicError

from ..constants import PAIR_LABELS
from ..db.pairs import pairs_mark_audit_complete, pairs_position_add
from ..helpers import escape_markdown
from ..networks import NETWORKS, V4_HOOKS, V4_NOT_ENOUGH_LIQUIDITY, V4_QUOTER_ABI
from ..settings import OPTIONS
from . import open_w3
from .audit import get_security_audit, get_token_metrics


async def audit_v4(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Check v4 pools; the ones that pass are watched and tracked as pretend buys.

    Returns "text" (one line per pool) and "watched".
    """
    results = []
    watched = []
    min_liq_eth = Decimal(str(OPTIONS.get("min_liquidity_eth", 0.5)))
    max_holder = float(OPTIONS.get("max_top_holder_pct", 10))
    buy_amount = Decimal(str(OPTIONS.get("buy_amount_eth", 0.0001)))

    for entry in entries:
        symbol = escape_markdown(entry.get("symbol") or "???")
        token_addr = entry["target_address"]
        try:
            conf = NETWORKS[entry.get("network") or "Base"]
            c0, c1, fee, spacing, hook = (entry.get("pool_key") or "").split(",")
            launchpad = V4_HOOKS.get(hook.lower(), "")
            if entry.get("repeat_name") or any(
                unicodedata.category(ch) in ("Cf", "Zs") and ch != " "
                for ch in entry.get("symbol") or ""
            ):
                await pairs_mark_audit_complete(token_addr, "spam")
                results.append(f"🚫 {symbol} — {PAIR_LABELS['spam']} · v4")
                continue
            w3 = await open_w3(conf)
            quoter = w3.eth.contract(
                address=Web3.to_checksum_address(conf["v4_quoter"]),
                abi=V4_QUOTER_ABI,
            )
            key = (
                Web3.to_checksum_address(c0),
                Web3.to_checksum_address(c1),
                int(fee),
                int(spacing),
                Web3.to_checksum_address(hook),
            )
            try:
                await quoter.functions.quoteExactOutputSingle(
                    (
                        key,
                        token_addr.lower() == c0.lower(),
                        int(min_liq_eth * 10**18),
                        b"",
                    )
                ).call()
                status = None
            except ContractLogicError as e:
                status = (
                    "low-liquidity"
                    if V4_NOT_ENOUGH_LIQUIDITY in str(e.data or e)
                    else "hook-blocked"
                )

            if status is None:
                sec_status, is_renounced, gp_data = await get_security_audit(
                    token_addr, conf["chain_id"]
                )
                sell_tax = float((gp_data or {}).get("sell_tax") or 0) * 100
                if gp_data is None:
                    status = "no-data"
                elif sec_status != "clear" and not (
                    sec_status == "owner-controls" and launchpad != "Uniswap"
                ):
                    status = sec_status
                elif sell_tax > 10:
                    status = "high-tax"
                elif gp_data.get("top_holder_pct", 0) > max_holder:
                    status = "whale"

            if status is None:
                buy_wei = int(buy_amount * 10**18)
                token_is_c0 = token_addr.lower() == c0.lower()
                try:
                    tokens, _ = await quoter.functions.quoteExactInputSingle(
                        (key, not token_is_c0, buy_wei, b"")
                    ).call()
                    back, _ = await quoter.functions.quoteExactInputSingle(
                        (key, token_is_c0, tokens, b"")
                    ).call()
                    if back < buy_wei * 0.8:
                        status = "high-tax"
                except ContractLogicError:
                    status = "honey-pot"

            if status:
                await pairs_mark_audit_complete(token_addr, status)
                label = PAIR_LABELS.get(status, status)
                results.append(f"🚫 {symbol} — {escape_markdown(label)} · v4")
                continue

            await pairs_mark_audit_complete(token_addr, "watched")
            metrics = await get_token_metrics(entry, conf)
            if metrics["success"]:
                await pairs_position_add(
                    {
                        "target_address": token_addr,
                        "pair_address": entry["pair_address"],
                        "pool_key": entry["pool_key"],
                        "symbol": entry.get("symbol"),
                        "network": entry.get("network") or "Base",
                        "eth_spent": float(buy_amount),
                        "status": "paper",
                    },
                    metrics,
                    sec_status,
                    is_renounced,
                    gp_data,
                )
            watched.append(
                f"${symbol} · {launchpad} v4 · {float(min_liq_eth):g}+ ETH liquidity"
            )
            results.append(f"👀 {watched[-1]} · would buy")

        except Exception as e:
            sentry_sdk.capture_exception(e)
            results.append(f"⚠️ {symbol} — {escape_markdown(e)} · v4")

    return {"text": "\n".join(results), "watched": watched}

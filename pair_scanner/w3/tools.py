import asyncio
import time

import sentry_sdk
from config import BOT_SETTINGS
from config.w3 import CHAINLINK_ABI, CHAINLINK_ETH_USD, NETWORKS, WETH_WITHDRAWAL_TOPIC
from web3 import Web3

from ..db.settings import settings_update
from . import open_w3, pool_kind


async def dex_name(conf, pair_addr, short=False):
    try:
        w3 = await open_w3(conf)
        stable, fee = await pool_kind(w3, conf, pair_addr)
    except Exception:
        return None
    if stable is not None:
        kind = "stable" if stable else "volatile"
        return f"Aero {kind}" if short else f"Aerodrome ({kind})"
    if short:
        return "V2" if fee is None else f"V3 {fee / 10_000:g}%"
    return "Uniswap V2" if fee is None else f"Uniswap V3 ({fee / 10_000:g}%)"


async def fetch_network_bal(name, conf, address):
    w3 = await open_w3(conf)
    try:
        eth_val = float(w3.from_wei(await w3.eth.get_balance(address), "ether"))
        return name, eth_val
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return name, None


async def fetch_eth_usd_price() -> float:
    try:
        w3 = await open_w3(NETWORKS["Ethereum"])
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(CHAINLINK_ETH_USD),
            abi=CHAINLINK_ABI,
        )
        _, answer, _, _, _ = await contract.functions.latestRoundData().call()
        return float(answer) / 1e8
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return 0.0


async def eth_balance_delta(current_eth: float):
    try:
        snap = float(BOT_SETTINGS.get("eth_bal_snapshot") or 0)
        snap_ts = float(BOT_SETTINGS.get("eth_bal_snapshot_ts") or 0)
    except (ValueError, TypeError):
        snap, snap_ts = 0.0, 0.0

    now = time.time()
    day = 24 * 60 * 60

    if snap <= 0 or snap_ts <= 0:
        await settings_update("eth_bal_snapshot", round(current_eth, 8))
        await settings_update("eth_bal_snapshot_ts", now)
        return None

    pct = (current_eth - snap) / snap * 100

    if now - snap_ts >= day:
        await settings_update("eth_bal_snapshot", round(current_eth, 8))
        await settings_update("eth_bal_snapshot_ts", now)

    return pct


def _tx_fee(receipt):
    l1_fee = receipt.get("l1Fee") or 0
    l1_fee = int(l1_fee, 16) if isinstance(l1_fee, str) else int(l1_fee)
    return receipt["gasUsed"] * receipt["effectiveGasPrice"] + l1_fee


async def trade_result(conf, buy_tx, sell_tx):
    if not buy_tx or not sell_tx:
        return None
    try:
        w3 = await open_w3(conf)
        weth = conf["weth"].lower()
        buy_tx = "0x" + buy_tx.removeprefix("0x")
        sell_tx = "0x" + sell_tx.removeprefix("0x")

        buy, buy_receipt, sell_receipt = await asyncio.gather(
            w3.eth.get_transaction(buy_tx),
            w3.eth.get_transaction_receipt(buy_tx),
            w3.eth.get_transaction_receipt(sell_tx),
        )
        spent = buy["value"] + _tx_fee(buy_receipt)

        withdrawals = [
            int.from_bytes(bytes(log["data"]), "big")
            for log in sell_receipt["logs"]
            if log["address"].lower() == weth
            and "0x" + bytes(log["topics"][0]).hex() == WETH_WITHDRAWAL_TOPIC
        ]
        if not withdrawals:
            return None
        received = withdrawals[-1] - _tx_fee(sell_receipt)

        return {
            "eth_spent": spent / 10**18,
            "eth_received": received / 10**18,
            "pnl": round((received - spent) / spent * 100, 2) if spent else None,
        }
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return None

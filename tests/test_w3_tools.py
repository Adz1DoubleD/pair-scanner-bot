import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import SAMPLE_ADDR, try_urls
from web3 import Web3

import pair_scanner.w3 as w3_core
import pair_scanner.w3.tools as w3_tools
from pair_scanner.networks import (
    CHAINLINK_ABI,
    CHAINLINK_ETH_USD,
    NETWORKS,
)

WITHDRAWAL = "0x7fcf532c15f0a6db0bd6d0e038bea71d30d808c7d98cb3bf7268a95bf5081b65"


def weth_log(address, wad):
    return {
        "address": address,
        "topics": [bytes.fromhex(WITHDRAWAL[2:])],
        "data": wad.to_bytes(32, "big"),
    }


@pytest.mark.asyncio
async def test_trade_result_counts_gas_and_the_last_unwrap(monkeypatch):
    buy_receipt = {
        "gasUsed": 125_000,
        "effectiveGasPrice": 6_000_000,
        "l1Fee": "0x2710",
    }
    sell_receipt = {
        "gasUsed": 150_000,
        "effectiveGasPrice": 6_000_000,
        "l1Fee": 10_000,
        "logs": [weth_log(SAMPLE_ADDR, 5 * 10**12), weth_log(SAMPLE_ADDR, 9 * 10**12)],
    }
    w3 = SimpleNamespace(
        eth=SimpleNamespace(
            get_transaction=AsyncMock(return_value={"value": 10**13}),
            get_transaction_receipt=AsyncMock(side_effect=[buy_receipt, sell_receipt]),
        )
    )
    monkeypatch.setattr(w3_tools, "open_w3", AsyncMock(return_value=w3))

    result = await w3_tools.trade_result({"weth": SAMPLE_ADDR}, "abc", "0xdef")

    assert result["eth_spent"] == pytest.approx(1.0750000001e-05)
    assert result["eth_received"] == pytest.approx(8.09999999e-06)
    assert result["pnl"] == -24.65


@pytest.mark.asyncio
async def test_trade_result_adds_eth_from_an_earlier_half_sale(monkeypatch):
    sell_receipt = {
        "gasUsed": 0,
        "effectiveGasPrice": 0,
        "logs": [weth_log(SAMPLE_ADDR, 4 * 10**12)],
    }
    w3 = SimpleNamespace(
        eth=SimpleNamespace(
            get_transaction=AsyncMock(return_value={"value": 10**13}),
            get_transaction_receipt=AsyncMock(
                side_effect=[{"gasUsed": 0, "effectiveGasPrice": 0}, sell_receipt]
            ),
        )
    )
    monkeypatch.setattr(w3_tools, "open_w3", AsyncMock(return_value=w3))

    result = await w3_tools.trade_result(
        {"weth": SAMPLE_ADDR}, "abc", "0xdef", earlier_eth=Decimal("0.000008")
    )

    assert result["eth_received"] == pytest.approx(1.2e-05)
    assert result["pnl"] == 20.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stable, short, label",
    [
        (False, True, "Aero volatile"),
        (True, True, "Aero stable"),
        (False, False, "Aerodrome (volatile)"),
        (True, False, "Aerodrome (stable)"),
    ],
)
async def test_dex_name_labels_aerodrome_pools(monkeypatch, stable, short, label):
    fee = AsyncMock()
    monkeypatch.setattr(w3_tools, "open_w3", AsyncMock(return_value=object()))
    monkeypatch.setattr(w3_core, "aero_stable", AsyncMock(return_value=stable))
    monkeypatch.setattr(w3_core, "pool_fee", fee)

    assert await w3_tools.dex_name({}, SAMPLE_ADDR, short=short) == label
    fee.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fee, short, label",
    [
        (None, True, "V2"),
        (3000, True, "V3 0.3%"),
        (None, False, "Uniswap V2"),
        (10000, False, "Uniswap V3 (1%)"),
    ],
)
async def test_dex_name_still_labels_uniswap_pools(monkeypatch, fee, short, label):
    monkeypatch.setattr(w3_tools, "open_w3", AsyncMock(return_value=object()))
    monkeypatch.setattr(w3_core, "aero_stable", AsyncMock(return_value=None))
    monkeypatch.setattr(w3_core, "pool_fee", AsyncMock(return_value=fee))

    assert await w3_tools.dex_name({}, SAMPLE_ADDR, short=short) == label


def test_live_eth_usd_price():
    conf = NETWORKS["Ethereum"]
    urls = [conf["rpc"]["url"]] + [f["url"] for f in conf.get("rpc_fallbacks", [])]

    async def call(w3):
        c = w3.eth.contract(
            address=Web3.to_checksum_address(CHAINLINK_ETH_USD), abi=CHAINLINK_ABI
        )
        return await c.functions.latestRoundData().call()

    try:
        round_data = asyncio.run(try_urls(urls, call))
    except Exception as e:
        pytest.skip(f"no live ETH RPC answered latestRoundData ({e})")

    _, answer, _, _, _ = round_data
    assert float(answer) / 1e8 > 100


@pytest.mark.asyncio
async def test_trade_result_without_a_sale_counts_the_buy_as_spent(monkeypatch):
    w3 = SimpleNamespace(
        eth=SimpleNamespace(
            get_transaction=AsyncMock(return_value={"value": 10**13}),
            get_transaction_receipt=AsyncMock(
                return_value={"gasUsed": 0, "effectiveGasPrice": 0}
            ),
        )
    )
    monkeypatch.setattr(w3_tools, "open_w3", AsyncMock(return_value=w3))

    result = await w3_tools.trade_result({"weth": SAMPLE_ADDR}, "abc")

    assert result == {"eth_spent": 1e-05, "eth_received": 0.0, "pnl": -100.0}
    w3.eth.get_transaction_receipt.assert_awaited_once()

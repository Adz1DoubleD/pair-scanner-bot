import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from web3.exceptions import TimeExhausted

import pair_scanner.w3.trade as trade

CONF = {
    "chain_id": 8453,
    "weth": "0x4200000000000000000000000000000000000006",
    "router": "0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24",
}
TOKEN = "0x1111111111111111111111111111111111111111"


def fake_w3():
    w3 = MagicMock()
    w3.eth.account.from_key.return_value = SimpleNamespace(
        address="0x2222222222222222222222222222222222222222"
    )
    w3.eth.account.sign_transaction.return_value = SimpleNamespace(raw_transaction=b"")
    w3.to_wei.return_value = 10**15
    gas = asyncio.get_running_loop().create_future()
    gas.set_result(1)
    w3.eth.gas_price = gas
    w3.eth.get_transaction_count = AsyncMock(return_value=5)
    w3.eth.send_raw_transaction = AsyncMock(return_value=bytes.fromhex("ab" * 32))
    w3.eth.wait_for_transaction_receipt = AsyncMock(
        side_effect=TimeExhausted("not mined in time")
    )
    fn = w3.eth.contract.return_value.functions
    fn.getAmountsOut.return_value.call = AsyncMock(return_value=[10**15, 10**18])
    fn.balanceOf.return_value.call = AsyncMock(return_value=10**18)
    fn.allowance.return_value.call = AsyncMock(return_value=10**30)
    swap = SimpleNamespace(
        estimate_gas=AsyncMock(return_value=100_000),
        build_transaction=AsyncMock(return_value={"to": "0x", "data": "0x"}),
    )
    fn.swapExactETHForTokensSupportingFeeOnTransferTokens.return_value = swap
    fn.swapExactTokensForETHSupportingFeeOnTransferTokens.return_value = swap
    return w3


@pytest.fixture
def chain(monkeypatch):
    reported = MagicMock()
    monkeypatch.setattr(trade.SETTINGS, "private_key", "0x" + "11" * 32)
    monkeypatch.setattr(trade, "OPTIONS", {"slippage": 5, "trading_enabled": 1})
    monkeypatch.setattr(trade, "pool_kind", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(trade.sentry_sdk, "capture_exception", reported)
    monkeypatch.setattr(trade, "nonce_manager", trade.NonceManager())
    return reported


@pytest.mark.asyncio
async def test_buy_that_times_out_after_sending_is_kept_as_pending(chain, monkeypatch):
    monkeypatch.setattr(trade, "open_w3", AsyncMock(return_value=fake_w3()))

    tx_hash, status = await trade.execute_buy(CONF, TOKEN, 0.001)

    assert tx_hash == "ab" * 32
    assert status == "PENDING"
    assert isinstance(chain.call_args.args[0], TimeExhausted)


@pytest.mark.asyncio
async def test_sell_that_times_out_after_sending_is_pending_not_failed(
    chain, monkeypatch
):
    monkeypatch.setattr(trade, "open_w3", AsyncMock(return_value=fake_w3()))
    pos = {"target_address": TOKEN, "pair_address": None}

    assert await trade.execute_sell(CONF, pos) == (None, "PENDING")
    assert isinstance(chain.call_args.args[0], TimeExhausted)

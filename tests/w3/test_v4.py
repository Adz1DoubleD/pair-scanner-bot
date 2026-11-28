import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from web3.exceptions import ContractLogicError

import pair_scanner.w3.audit as audit
import pair_scanner.w3.trade as trade
import pair_scanner.w3.v4 as v4
from pair_scanner.settings import OPTIONS

TOKEN = "0x" + "ab" * 20
WETH = "0x4200000000000000000000000000000000000006"
DOPPLER = "0xbdf938149ac6a781f94faa0ed45e6a0e984c6544"
NO_HOOK = "0x" + "00" * 20
CONF = {
    "chain_id": 8453,
    "v4_quoter": "0x0d5e0f971ed27fbff6c2837bf31316121532048d",
}


def pool(hook=DOPPLER, c0=TOKEN, c1=WETH):
    return {
        "target_address": TOKEN,
        "pair_address": "0x" + "ee" * 32,
        "network": "Base",
        "symbol": "TKN",
        "pool_key": f"{c0},{c1},8388608,200,{hook}",
    }


@pytest.fixture
def checks(monkeypatch):
    quote = AsyncMock(return_value=(10**20, 100_000))
    quoter = MagicMock()
    quoter.functions.quoteExactOutputSingle.return_value.call = quote
    trade = AsyncMock(return_value=(10**14, 100_000))
    quoter.functions.quoteExactInputSingle.return_value.call = trade
    w3 = SimpleNamespace(eth=SimpleNamespace(contract=MagicMock(return_value=quoter)))
    m = SimpleNamespace(
        quote=quote,
        quoter=quoter,
        audit=AsyncMock(
            return_value=("clear", True, {"sell_tax": 0, "top_holder_pct": 2})
        ),
        marks=AsyncMock(),
        trade=trade,
        metrics=AsyncMock(return_value={"success": True, "price_eth": "0.5"}),
        positions=AsyncMock(),
    )
    monkeypatch.setattr(v4, "NETWORKS", {"Base": CONF})
    monkeypatch.setattr(v4, "open_w3", AsyncMock(return_value=w3))
    monkeypatch.setattr(v4, "get_security_audit", m.audit)
    monkeypatch.setattr(v4, "pairs_mark_audit_complete", m.marks)
    monkeypatch.setattr(v4, "get_token_metrics", m.metrics)
    monkeypatch.setattr(v4, "pairs_position_add", m.positions)
    OPTIONS.update(min_liquidity_eth=0.5, max_top_holder_pct=10, buy_amount_eth=0.0001)
    return m


@pytest.mark.asyncio
async def test_a_good_pool_is_watched_never_bought(checks):
    result = await v4.audit_v4([pool()])

    checks.marks.assert_awaited_once_with(TOKEN, "watched")
    assert result["watched"] == ["$TKN · Doppler v4 · 0.5+ ETH liquidity"]
    assert result["text"] == "👀 $TKN · Doppler v4 · 0.5+ ETH liquidity · would buy"
    assert "buys" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "c0,c1,sells_token0", [(TOKEN, WETH, True), (NO_HOOK, TOKEN, False)]
)
async def test_the_quote_asks_for_the_minimum_eth_out_of_a_sale(
    checks, c0, c1, sells_token0
):
    await v4.audit_v4([pool(c0=c0, c1=c1)])

    ((key, zero_for_one, amount, hook_data),) = (
        checks.quoter.functions.quoteExactOutputSingle.call_args.args
    )
    assert zero_for_one is sells_token0
    assert amount == 5 * 10**17
    assert key[2:4] == (8388608, 200)
    assert hook_data == b""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,status",
    [
        (
            ContractLogicError("reverted", data="0x6190b2b0" + "7a5ed734" + "00" * 32),
            "low-liquidity",
        ),
        (ContractLogicError("reverted", data="0x6190b2b0a9e35b2f"), "hook-blocked"),
    ],
    ids=["not-enough-eth", "hook-reverts"],
)
async def test_a_failed_quote_rejects_without_the_other_checks(checks, error, status):
    checks.quote.side_effect = error

    result = await v4.audit_v4([pool()])

    checks.marks.assert_awaited_once_with(TOKEN, status)
    checks.audit.assert_not_awaited()
    assert result["watched"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hook,audit,status",
    [
        (DOPPLER, ("owner-controls", True, {"sell_tax": 0}), "watched"),
        (NO_HOOK, ("owner-controls", True, {"sell_tax": 0}), "owner-controls"),
        (DOPPLER, ("honey-pot", True, {"sell_tax": 0}), "honey-pot"),
        (DOPPLER, ("no-data", False, None), "no-data"),
        (DOPPLER, ("clear", True, {"sell_tax": 0.25}), "high-tax"),
        (DOPPLER, ("clear", True, {"top_holder_pct": 40}), "whale"),
    ],
    ids=[
        "launchpad-controls-allowed",
        "plain-pool-controls-rejected",
        "honeypot",
        "no-data",
        "high-tax",
        "whale",
    ],
)
async def test_security_rules_for_v4_tokens(checks, hook, audit, status):
    checks.audit.return_value = audit

    await v4.audit_v4([pool(hook=hook)])

    checks.marks.assert_awaited_once_with(TOKEN, status)


@pytest.mark.asyncio
async def test_a_network_error_leaves_the_pool_for_next_time(monkeypatch, checks):
    checks.quote.side_effect = TimeoutError("slow")
    reported = MagicMock()
    monkeypatch.setattr(v4.sentry_sdk, "capture_exception", reported)

    result = await v4.audit_v4([pool()])

    checks.marks.assert_not_awaited()
    reported.assert_called_once()
    assert result["text"] == "⚠️ TKN — slow · v4"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [{"symbol": "U\u200aS\u200aO\u200aE"}, {"repeat_name": 1}],
    ids=["hidden-characters", "name-seen-today"],
)
async def test_a_spam_name_is_rejected_without_a_quote(checks, extra):
    result = await v4.audit_v4([{**pool(), **extra}])

    checks.marks.assert_awaited_once_with(TOKEN, "spam")
    checks.quote.assert_not_awaited()
    assert result["watched"] == []
    assert "Spam Name · v4" in result["text"]


@pytest.mark.asyncio
async def test_a_good_pool_becomes_a_pretend_position(checks):
    await v4.audit_v4([pool()])

    entry, metrics, sec_status, is_renounced, gp_data = checks.positions.call_args.args
    assert entry["status"] == "paper"
    assert entry["eth_spent"] == 0.0001
    assert entry["pool_key"] == pool()["pool_key"]
    assert entry["pair_address"] == pool()["pair_address"]
    assert metrics["price_eth"] == "0.5"
    assert (sec_status, is_renounced) == ("clear", True)


@pytest.mark.asyncio
async def test_no_pretend_position_without_a_price(checks):
    checks.metrics.return_value = {"success": False}

    result = await v4.audit_v4([pool()])

    checks.marks.assert_awaited_once_with(TOKEN, "watched")
    checks.positions.assert_not_awaited()
    assert len(result["watched"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "c0,c1,buys_token1", [(TOKEN, WETH, False), (NO_HOOK, TOKEN, True)]
)
async def test_the_sell_check_buys_then_sells_the_tokens(checks, c0, c1, buys_token1):
    checks.trade.side_effect = [(5 * 10**20, 0), (10**14, 0)]

    await v4.audit_v4([pool(c0=c0, c1=c1)])

    buy, sell = checks.quoter.functions.quoteExactInputSingle.call_args_list
    ((_, buy_zero_for_one, buy_amount, _),) = buy.args
    ((_, sell_zero_for_one, sell_amount, _),) = sell.args
    assert (buy_zero_for_one, buy_amount) == (buys_token1, 10**14)
    assert (sell_zero_for_one, sell_amount) == (not buys_token1, 5 * 10**20)


@pytest.mark.asyncio
async def test_a_token_that_cannot_be_sold_is_a_honeypot(checks):
    checks.trade.side_effect = [(10**20, 0), ContractLogicError("reverted")]

    result = await v4.audit_v4([pool()])

    checks.marks.assert_awaited_once_with(TOKEN, "honey-pot")
    checks.positions.assert_not_awaited()
    assert result["watched"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "back,status", [(7 * 10**13, "high-tax"), (9 * 10**13, "watched")]
)
async def test_losing_over_a_fifth_on_the_round_trip_is_rejected(checks, back, status):
    checks.trade.side_effect = [(10**20, 0), (back, 0)]

    await v4.audit_v4([pool()])

    checks.marks.assert_awaited_once_with(TOKEN, status)


def chain(monkeypatch, module, quote):
    contract = MagicMock()
    contract.functions.balanceOf.return_value.call = AsyncMock(return_value=10**27)
    contract.functions.totalSupply.return_value.call = AsyncMock(return_value=10**27)
    contract.functions.decimals.return_value.call = AsyncMock(return_value=18)
    contract.functions.quoteExactInputSingle.return_value.call = quote
    gas = asyncio.get_running_loop().create_future()
    gas.set_result(10**9)
    w3 = SimpleNamespace(
        eth=SimpleNamespace(contract=MagicMock(return_value=contract), gas_price=gas)
    )
    monkeypatch.setattr(module, "open_w3", AsyncMock(return_value=w3))
    return contract


V4_CONF = {
    **CONF,
    "weth": WETH,
    "v4_state_view": "0xa3c0c9b65bad0b08107aa264b0f3db444b867a71",
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "c0,c1,price,liq",
    [(WETH, TOKEN, 0.25, 500.0), (TOKEN, WETH, 4.0, 2000.0)],
    ids=["eth-first", "token-first"],
)
async def test_v4_prices_come_from_the_pool_state(monkeypatch, c0, c1, price, liq):
    contract = chain(monkeypatch, audit, AsyncMock())
    contract.functions.getSlot0.return_value.call = AsyncMock(
        return_value=(2 * 2**96, 0, 0, 0)
    )
    contract.functions.getLiquidity.return_value.call = AsyncMock(
        return_value=1000 * 10**18
    )

    stats = await audit.get_token_metrics(pool(c0=c0, c1=c1), V4_CONF)

    assert stats["success"] is True
    assert float(stats["price_eth"]) == pytest.approx(price)
    assert float(stats["liq_eth"]) == pytest.approx(liq)
    assert contract.functions.getSlot0.call_args.args == (bytes.fromhex("ee" * 32),)


@pytest.mark.asyncio
async def test_a_v4_sale_is_quoted_on_the_v4_quoter(monkeypatch):
    quote = AsyncMock(return_value=(3 * 10**15, 0))
    contract = chain(monkeypatch, trade, quote)

    worth, error = await trade.execute_sell(V4_CONF, pool(), quote=True, amount=10**18)

    assert (worth, error) == (3 * 10**15, None)
    ((_, sells_token0, amount, _),) = (
        contract.functions.quoteExactInputSingle.call_args.args
    )
    assert (sells_token0, amount) == (True, 10**18)

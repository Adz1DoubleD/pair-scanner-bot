from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from web3.exceptions import ContractLogicError

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
    w3 = SimpleNamespace(eth=SimpleNamespace(contract=MagicMock(return_value=quoter)))
    m = SimpleNamespace(
        quote=quote,
        quoter=quoter,
        audit=AsyncMock(
            return_value=("clear", True, {"sell_tax": 0, "top_holder_pct": 2})
        ),
        marks=AsyncMock(),
    )
    monkeypatch.setattr(v4, "NETWORKS", {"Base": CONF})
    monkeypatch.setattr(v4, "open_w3", AsyncMock(return_value=w3))
    monkeypatch.setattr(v4, "get_security_audit", m.audit)
    monkeypatch.setattr(v4, "pairs_mark_audit_complete", m.marks)
    OPTIONS.update(min_liquidity_eth=0.5, max_top_holder_pct=10)
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

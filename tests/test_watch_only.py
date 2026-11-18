from unittest.mock import AsyncMock, MagicMock

import pytest

import pair_scanner.monitors as monitors
import pair_scanner.w3.audit as audit
import pair_scanner.w3.trade as trade
from pair_scanner.settings import OPTIONS

PAIR = {
    "target_address": "0x" + "aa" * 20,
    "pair_address": "0x" + "bb" * 20,
    "network": "Base",
    "symbol": "TKN",
}


@pytest.fixture
def watch_only():
    OPTIONS["trading_enabled"] = 0


@pytest.mark.asyncio
async def test_buys_are_refused_before_touching_the_chain(monkeypatch, watch_only):
    opened = AsyncMock()
    monkeypatch.setattr(trade, "open_w3", opened)

    tx, error = await trade.execute_buy({"chain_id": 8453}, PAIR["target_address"], 1)

    assert (tx, error) == (None, trade.WATCH_ONLY)
    opened.assert_not_awaited()


@pytest.fixture
def passing_audit(monkeypatch):
    contract = MagicMock()
    contract.functions.balanceOf.return_value.call = AsyncMock(return_value=5 * 10**18)
    w3 = MagicMock()
    w3.eth.contract.return_value = contract
    monkeypatch.setattr(audit, "open_w3", AsyncMock(return_value=w3))
    monkeypatch.setattr(
        audit,
        "get_security_audit",
        AsyncMock(
            return_value=(
                "clear",
                True,
                {"lp_locked_pct": 100, "buy_tax": 0, "sell_tax": 0},
            )
        ),
    )
    monkeypatch.setattr(audit, "check_honeypot", AsyncMock(return_value=True))
    monkeypatch.setattr(audit, "simulate_sell", AsyncMock(return_value=True))
    monkeypatch.setattr(
        audit,
        "get_token_metrics",
        AsyncMock(return_value={"success": True, "ratio": 50}),
    )
    monkeypatch.setattr(audit, "pool_kind", AsyncMock(return_value=(None, None)))
    marks = AsyncMock()
    monkeypatch.setattr(audit, "pairs_mark_audit_complete", marks)
    monkeypatch.setattr(audit, "pairs_mark_for_retry", AsyncMock())
    buy = AsyncMock()
    monkeypatch.setattr(audit, "execute_buy", buy)
    return marks, buy


@pytest.mark.asyncio
async def test_a_passing_pair_is_watched_not_bought(passing_audit, watch_only):
    marks, buy = passing_audit

    result = await audit.audit_pair(None, [dict(PAIR)])

    buy.assert_not_awaited()
    marks.assert_awaited_once_with(PAIR["target_address"], "watched")
    assert result["buys"] == []
    assert result["watched"] == ["$TKN · 5.00 ETH liquidity"]
    assert "👀 $TKN · 5.00 ETH liquidity · would buy" in result["text"]


@pytest.mark.asyncio
async def test_a_manual_buy_says_trading_is_off(passing_audit, watch_only):
    marks, buy = passing_audit

    result = await audit.audit_pair(None, dict(PAIR), force_buy=True)

    buy.assert_not_awaited()
    marks.assert_not_awaited()
    assert "trading is off" in result["text"]


@pytest.mark.asyncio
async def test_the_strategy_watches_instead_of_buying(monkeypatch, watch_only):
    monkeypatch.setattr(monitors, "NETWORKS", {"Base": {"chain_id": 8453}})
    monkeypatch.setattr(monitors, "pairs_get_by_status", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        monitors,
        "fetch_candidates",
        AsyncMock(
            return_value=(
                [
                    {
                        "token_addr": PAIR["target_address"],
                        "pair_addr": PAIR["pair_address"],
                        "symbol": "TKN",
                        "liq_usd": 500_000.0,
                        "change_h1": 9.0,
                    }
                ],
                None,
            )
        ),
    )
    monkeypatch.setattr(
        monitors,
        "get_security_audit",
        AsyncMock(return_value=("clear", True, {"sell_tax": 0})),
    )
    monkeypatch.setattr(monitors, "check_honeypot", AsyncMock(return_value=True))
    monkeypatch.setattr(monitors, "simulate_sell", AsyncMock(return_value=True))
    added = AsyncMock()
    monkeypatch.setattr(monitors, "pairs_strat_add", added)
    buy = AsyncMock()
    monkeypatch.setattr(monitors, "execute_buy", buy)

    result = await monitors.run_strategy_monitor(None)

    buy.assert_not_awaited()
    added.assert_awaited_once_with(
        PAIR["target_address"], PAIR["pair_address"], "TKN", "watched"
    )
    assert result["buys"] == [] and len(result["watched"]) == 1

from unittest.mock import AsyncMock, MagicMock

import pytest

import pair_scanner.monitors as monitors
import pair_scanner.w3.audit as audit
import pair_scanner.w3.trade as trade
from pair_scanner.settings import OPTIONS, SETTINGS

PAIR = {
    "target_address": "0x" + "aa" * 20,
    "pair_address": "0x" + "bb" * 20,
    "network": "Base",
    "symbol": "TKN",
}


@pytest.fixture
def watch_only():
    OPTIONS.update(pairs_mode=1, strategy_mode=1)


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
    assert "the Pairs Monitor is on 👀 Watch" in result["text"]


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


@pytest.mark.asyncio
async def test_the_strategy_only_mentions_each_watched_token_once(
    monkeypatch, watch_only
):
    monkeypatch.setattr(monitors, "NETWORKS", {"Base": {"chain_id": 8453}})

    async def by_status(status, table):
        if (status, table) == ("watched", "pairs"):
            return [{"target_address": PAIR["target_address"].upper()}]
        return []

    monkeypatch.setattr(monitors, "pairs_get_by_status", by_status)
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
    audit_call = AsyncMock()
    monkeypatch.setattr(monitors, "get_security_audit", audit_call)

    result = await monitors.run_strategy_monitor(None)

    audit_call.assert_not_awaited()
    assert result.get("watched", []) == []
    assert "Nothing to buy" in result["text"]


@pytest.mark.asyncio
async def test_with_trading_on_watched_tokens_can_be_bought(monkeypatch):
    calls = []

    async def by_status(status, table):
        calls.append((status, table))
        return []

    monkeypatch.setattr(monitors, "NETWORKS", {"Base": {"chain_id": 8453}})
    monkeypatch.setattr(monitors, "pairs_get_by_status", by_status)
    monkeypatch.setattr(
        monitors, "fetch_candidates", AsyncMock(return_value=([], None))
    )

    await monitors.run_strategy_monitor(None)

    assert ("watched", "pairs") not in calls


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["execute_buy", "execute_sell"])
async def test_test_mode_refuses_every_trade(monkeypatch, name):
    OPTIONS.update(pairs_mode=2, strategy_mode=2)
    monkeypatch.setattr(SETTINGS, "test_mode", True)
    monkeypatch.setattr(SETTINGS, "private_key", "0x" + "11" * 32)
    opened = AsyncMock()
    monkeypatch.setattr(trade, "open_w3", opened)

    args = (PAIR["target_address"], 1) if name == "execute_buy" else (dict(PAIR),)
    tx, error = await getattr(trade, name)({"chain_id": 8453}, *args)

    assert (tx, error) == (None, trade.OFF)
    opened.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_sell_without_a_key_is_refused(monkeypatch):
    monkeypatch.setattr(SETTINGS, "private_key", None)
    opened = AsyncMock()
    monkeypatch.setattr(trade, "open_w3", opened)

    tx, error = await trade.execute_sell({"chain_id": 8453}, dict(PAIR))

    assert (tx, error) == (None, trade.NO_KEY)
    opened.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pairs,strategy,refused",
    [(0, 0, True), (1, 0, False), (0, 1, False), (2, 0, False)],
)
async def test_selling_runs_unless_both_monitors_are_off(
    monkeypatch, pairs, strategy, refused
):
    OPTIONS.update(pairs_mode=pairs, strategy_mode=strategy)
    opened = AsyncMock(side_effect=RuntimeError("reached the chain"))
    monkeypatch.setattr(trade, "open_w3", opened)

    if refused:
        assert await trade.execute_sell({"chain_id": 8453}, dict(PAIR)) == (
            None,
            trade.OFF,
        )
        opened.assert_not_awaited()
    else:
        with pytest.raises(RuntimeError, match="reached the chain"):
            await trade.execute_sell({"chain_id": 8453}, dict(PAIR))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "monitor,error", [("pairs", None), ("strategy", "Watch mode: no buys")]
)
async def test_each_monitor_buys_only_in_its_own_live_mode(monkeypatch, monitor, error):
    OPTIONS.update(pairs_mode=2, strategy_mode=1)
    monkeypatch.setattr(SETTINGS, "private_key", "0x" + "11" * 32)
    opened = AsyncMock(side_effect=RuntimeError("reached the chain"))
    monkeypatch.setattr(trade, "open_w3", opened)

    if error is None:
        with pytest.raises(RuntimeError, match="reached the chain"):
            await trade.execute_buy(
                {"chain_id": 8453}, PAIR["target_address"], 1, monitor=monitor
            )
    else:
        assert await trade.execute_buy(
            {"chain_id": 8453}, PAIR["target_address"], 1, monitor=monitor
        ) == (None, error)
        opened.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_token_with_one_big_holder_is_rejected(passing_audit, monkeypatch):
    marks, buy = passing_audit
    monkeypatch.setattr(
        audit,
        "get_security_audit",
        AsyncMock(
            return_value=(
                "clear",
                True,
                {
                    "lp_locked_pct": 100,
                    "buy_tax": 0,
                    "sell_tax": 0,
                    "top_holder_pct": 25,
                },
            )
        ),
    )

    result = await audit.audit_pair(None, [dict(PAIR)])

    buy.assert_not_awaited()
    marks.assert_awaited_once_with(PAIR["target_address"], "whale")
    assert "Big Holder" in result["text"]

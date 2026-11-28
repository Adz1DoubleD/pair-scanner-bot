from unittest.mock import AsyncMock, MagicMock

import pytest

import pair_scanner.trading.strategy as strategy
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
    monkeypatch.setattr(audit, "pairs_position_add", AsyncMock())
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
    monkeypatch.setattr(strategy, "NETWORKS", {"Base": {"chain_id": 8453}})
    monkeypatch.setattr(strategy, "pairs_get_by_status", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        strategy,
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
                        "created_at": "2024-01-01T00:00:00Z",
                    }
                ],
                None,
            )
        ),
    )
    monkeypatch.setattr(
        strategy,
        "get_security_audit",
        AsyncMock(return_value=("clear", True, {"sell_tax": 0})),
    )
    monkeypatch.setattr(strategy, "check_honeypot", AsyncMock(return_value=True))
    monkeypatch.setattr(strategy, "simulate_sell", AsyncMock(return_value=True))
    monkeypatch.setattr(
        strategy,
        "get_token_metrics",
        AsyncMock(return_value={"liq_eth": 1000.0, "success": True}),
    )
    monkeypatch.setattr(strategy, "fetch_eth_usd_price", AsyncMock(return_value=3000.0))
    added = AsyncMock()
    monkeypatch.setattr(strategy, "pairs_strat_add", added)
    paper = AsyncMock()
    monkeypatch.setattr(strategy, "pairs_position_add", paper)
    buy = AsyncMock()
    monkeypatch.setattr(strategy, "execute_buy", buy)

    result = await strategy.run_strategy_monitor(None)

    buy.assert_not_awaited()
    added.assert_awaited_once_with(
        PAIR["target_address"], PAIR["pair_address"], "TKN", "watched"
    )
    assert result["buys"] == [] and len(result["watched"]) == 1


@pytest.mark.asyncio
async def test_the_strategy_only_mentions_each_watched_token_once(
    monkeypatch, watch_only
):
    monkeypatch.setattr(strategy, "NETWORKS", {"Base": {"chain_id": 8453}})

    async def by_status(status, table):
        if (status, table) == ("watched", "pairs"):
            return [{"target_address": PAIR["target_address"].upper()}]
        return []

    monkeypatch.setattr(strategy, "pairs_get_by_status", by_status)
    monkeypatch.setattr(
        strategy,
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
                        "created_at": "2024-01-01T00:00:00Z",
                    }
                ],
                None,
            )
        ),
    )
    audit_call = AsyncMock()
    monkeypatch.setattr(strategy, "get_security_audit", audit_call)

    result = await strategy.run_strategy_monitor(None)

    audit_call.assert_not_awaited()
    assert result.get("watched", []) == []
    assert "Nothing to buy" in result["text"]


@pytest.mark.asyncio
async def test_with_trading_on_watched_tokens_can_be_bought(monkeypatch):
    calls = []

    async def by_status(status, table):
        calls.append((status, table))
        return []

    monkeypatch.setattr(strategy, "NETWORKS", {"Base": {"chain_id": 8453}})
    monkeypatch.setattr(strategy, "pairs_get_by_status", by_status)
    monkeypatch.setattr(
        strategy, "fetch_candidates", AsyncMock(return_value=([], None))
    )

    await strategy.run_strategy_monitor(None)

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


@pytest.mark.asyncio
async def test_a_watched_pair_is_followed_as_a_pretend_position(
    passing_audit, watch_only
):
    await audit.audit_pair(None, [dict(PAIR)])

    entry = audit.pairs_position_add.await_args.args[0]
    assert entry["status"] == "paper"
    assert entry["eth_spent"] == OPTIONS["buy_amount_eth"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sellable,honeypot_safe,status",
    [
        (None, True, "sell-unchecked"),
        (False, True, "honey-pot"),
        (None, None, "sell-unchecked"),
        (True, None, "sell-unchecked"),
        (True, False, "honey-pot"),
    ],
)
async def test_an_unchecked_sale_is_retried_not_called_a_honeypot(
    monkeypatch, passing_audit, watch_only, sellable, honeypot_safe, status
):
    marks, _ = passing_audit
    monkeypatch.setattr(audit, "simulate_sell", AsyncMock(return_value=sellable))
    monkeypatch.setattr(audit, "check_honeypot", AsyncMock(return_value=honeypot_safe))

    await audit.audit_pair(None, [dict(PAIR)])

    marks.assert_awaited_once_with(PAIR["target_address"], status)


def test_an_unchecked_sale_is_always_retried():
    from pair_scanner.constants import PAIR_RETRY_ALWAYS

    assert "sell-unchecked" in PAIR_RETRY_ALWAYS


@pytest.mark.asyncio
async def test_a_pool_without_liquidity_skips_the_other_checks(
    monkeypatch, passing_audit, watch_only
):
    marks, _ = passing_audit
    contract = MagicMock()
    contract.functions.balanceOf.return_value.call = AsyncMock(return_value=10**12)
    w3 = MagicMock()
    w3.eth.contract.return_value = contract
    monkeypatch.setattr(audit, "open_w3", AsyncMock(return_value=w3))

    result = await audit.audit_pair(None, [dict(PAIR)])

    marks.assert_awaited_once_with(PAIR["target_address"], "low-liquidity")
    audit.get_security_audit.assert_not_awaited()
    audit.simulate_sell.assert_not_awaited()
    assert "🚫 TKN — Low Liquidity" in result["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [{"symbol": "U\u200aS\u200aO\u200aE"}, {"symbol": "A\u200bB"}, {"repeat_name": 1}],
    ids=["hair-spaces", "zero-width-space", "name-seen-today"],
)
async def test_a_spam_name_is_rejected_before_any_check(
    passing_audit, watch_only, extra
):
    marks, _ = passing_audit

    result = await audit.audit_pair(None, [{**PAIR, **extra}])

    marks.assert_awaited_once_with(PAIR["target_address"], "spam")
    audit.open_w3.assert_not_awaited()
    assert "Spam Name" in result["text"]


@pytest.mark.asyncio
async def test_a_plain_space_in_a_name_is_not_spam(passing_audit, watch_only):
    marks, _ = passing_audit

    await audit.audit_pair(None, [{**PAIR, "symbol": "Truth Oil"}])

    marks.assert_awaited_once_with(PAIR["target_address"], "watched")


@pytest.mark.asyncio
async def test_a_preview_still_runs_every_check_and_names_the_spam(
    passing_audit, watch_only
):
    preview = await audit.audit_pair(None, {**PAIR, "repeat_name": 1}, preview=True)

    assert "WOULD REJECT" in preview
    assert "Spam Name" in preview
    audit.get_security_audit.assert_awaited_once()

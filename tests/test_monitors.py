from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from conftest import freeze_datetime, make_ctx, utc

import pair_scanner.monitors as auto


@pytest.fixture
def strat(monkeypatch):
    conf = {"chain_id": 8453}
    settings = {
        "strat_min_liq_usd": 250_000,
        "strat_min_momentum_h1": 3.0,
        "max_positions": 5,
        "buy_amount_eth": 0.0001,
        "trading_enabled": 1,
    }
    monkeypatch.setattr(auto, "OPTIONS", settings)
    monkeypatch.setattr(auto, "NETWORKS", {"Base": conf})

    gp = {"sell_tax": 0.0}
    m = SimpleNamespace(
        conf=conf,
        settings=settings,
        gp=gp,
        held=AsyncMock(return_value=[]),
        candidates=AsyncMock(return_value=([], None)),
        audit=AsyncMock(return_value=("clear", True, gp)),
        honeypot=AsyncMock(return_value=True),
        simulate=AsyncMock(return_value=True),
        buy=AsyncMock(return_value=("0xbuytx", None)),
        metrics=AsyncMock(
            return_value={"price_eth": 1.0, "liq_eth": 1000.0, "success": True}
        ),
        position_add=AsyncMock(),
        strat_add=AsyncMock(),
        capture=MagicMock(),
    )
    monkeypatch.setattr(auto, "pairs_get_by_status", m.held)
    monkeypatch.setattr(auto, "fetch_candidates", m.candidates)
    monkeypatch.setattr(auto, "get_security_audit", m.audit)
    monkeypatch.setattr(auto, "check_honeypot", m.honeypot)
    monkeypatch.setattr(auto, "simulate_sell", m.simulate)
    monkeypatch.setattr(auto, "execute_buy", m.buy)
    monkeypatch.setattr(auto, "get_token_metrics", m.metrics)
    monkeypatch.setattr(auto, "fetch_eth_usd_price", AsyncMock(return_value=3000.0))
    monkeypatch.setattr(auto, "pairs_position_add", m.position_add)
    monkeypatch.setattr(auto, "pairs_strat_add", m.strat_add)
    monkeypatch.setattr(auto, "dex_name", AsyncMock(return_value=None))
    monkeypatch.setattr(
        auto, "sentry_sdk", SimpleNamespace(capture_exception=m.capture)
    )
    return m


def cand(symbol, liq=500_000, h1=10.0, created_at="2024-01-01T00:00:00Z"):
    return {
        "symbol": symbol,
        "token_addr": f"0x{symbol.lower()}",
        "pair_addr": f"0xpair-{symbol.lower()}",
        "liq_usd": liq,
        "change_h1": h1,
        "created_at": created_at,
    }


@pytest.mark.asyncio
async def test_strategy_filters_by_liquidity_and_momentum(strat):
    strat.candidates.return_value = (
        [
            cand("LOW", liq=249_999),
            cand("FLAT", h1=2.9),
            cand("EDGE", liq=250_000, h1=3.0),
        ],
        None,
    )

    summary = (await auto.run_strategy_monitor(make_ctx()))["text"]

    assert "Fetched: `3` pools" in summary
    assert "Found: `1`" in summary
    strat.buy.assert_awaited_once_with(
        strat.conf, "0xedge", 0.0001, "0xpair-edge", monitor="strategy"
    )


@pytest.mark.asyncio
async def test_strategy_uses_configured_thresholds_and_buy_amount(strat):
    strat.settings.update(
        strat_min_liq_usd=1_000_000, strat_min_momentum_h1=20.0, buy_amount_eth=0.5
    )
    strat.candidates.return_value = (
        [cand("A", liq=999_999, h1=50), cand("B", liq=2_000_000, h1=20)],
        None,
    )

    await auto.run_strategy_monitor(make_ctx())

    strat.buy.assert_awaited_once_with(
        strat.conf, "0xb", 0.5, "0xpair-b", monitor="strategy"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["monitoring", "completed"])
async def test_strategy_never_rebuys_a_traded_token(strat, status):
    strat.held.return_value = [{"target_address": "0xA", "status": status}]
    strat.candidates.return_value = ([cand("A")], None)

    summary = (await auto.run_strategy_monitor(make_ctx()))["text"]

    assert "🚫 Nothing to buy" in summary
    assert "Found: `0`" in summary
    strat.buy.assert_not_awaited()


@pytest.mark.asyncio
async def test_strategy_looks_up_monitoring_positions(strat):
    await auto.run_strategy_monitor(make_ctx())

    strat.held.assert_awaited_once_with(status=None, table="positions")


@pytest.mark.asyncio
async def test_strategy_handles_no_held_result(strat):
    strat.held.return_value = None
    strat.candidates.return_value = ([cand("A")], None)

    summary = (await auto.run_strategy_monitor(make_ctx()))["text"]

    assert "🛒 Bought: `1` (1/5 positions)" in summary


@pytest.mark.asyncio
async def test_strategy_stops_at_max_positions(strat):
    strat.settings["max_positions"] = 2
    strat.held.return_value = [
        {"target_address": "0x1", "status": "monitoring"},
        {"target_address": "0x2", "status": "monitoring"},
    ]
    strat.candidates.return_value = ([cand("A")], None)

    summary = (await auto.run_strategy_monitor(make_ctx()))["text"]

    assert "⏸️ At max positions (2/2)" in summary
    strat.buy.assert_not_awaited()


@pytest.mark.asyncio
async def test_strategy_nothing_to_buy(strat):
    strat.candidates.return_value = ([], None)

    summary = (await auto.run_strategy_monitor(make_ctx()))["text"]

    assert summary.startswith("🧭 *Strategy Monitor*")
    assert "Fetched: `0` pools" in summary
    assert "🚫 Nothing to buy" in summary


@pytest.mark.asyncio
async def test_strategy_shows_fetch_error(strat):
    strat.candidates.return_value = ([], "rate limited")

    summary = (await auto.run_strategy_monitor(make_ctx()))["text"]

    assert "⚠️ Error: `rate limited`" in summary


@pytest.mark.asyncio
async def test_strategy_buys_best_momentum_first_up_to_free_slots(strat):
    strat.settings["max_positions"] = 3
    strat.held.return_value = [{"target_address": "0xheld", "status": "monitoring"}]
    strat.candidates.return_value = (
        [cand("A", h1=5), cand("B", h1=20), cand("C", h1=10)],
        None,
    )

    summary = (await auto.run_strategy_monitor(make_ctx()))["text"]

    assert [c.args[1] for c in strat.buy.await_args_list] == ["0xb", "0xc"]
    assert "Found: `3`" in summary
    assert "🛒 Bought: `2` (3/3 positions)" in summary


@pytest.mark.asyncio
async def test_strategy_keeps_going_past_rejections_to_fill_slots(strat):
    strat.settings["max_positions"] = 1
    strat.candidates.return_value = ([cand("A", h1=20), cand("B", h1=10)], None)
    strat.honeypot.side_effect = [False, True]

    summary = (await auto.run_strategy_monitor(make_ctx()))["text"]

    strat.buy.assert_awaited_once_with(
        strat.conf, "0xb", 0.0001, "0xpair-b", monitor="strategy"
    )
    assert "🛡️ Passed: `1`" in summary
    assert "🛒 Bought: `1` (1/1 positions)" in summary


@pytest.mark.asyncio
async def test_strategy_returns_a_chart_for_each_buy_only(strat):
    strat.candidates.return_value = ([cand("A", h1=20), cand("B", h1=10)], None)
    strat.honeypot.side_effect = [True, False]

    result = await auto.run_strategy_monitor(make_ctx())

    assert result["charts"] == [("A", "0xpair-a")]


@pytest.mark.asyncio
async def test_strategy_returns_no_charts_when_nothing_bought(strat):
    assert (await auto.run_strategy_monitor(make_ctx()))["charts"] == []


@pytest.mark.asyncio
async def test_strategy_buy_line_shows_the_dex(strat, monkeypatch):
    dex = AsyncMock(return_value="V3 0.3%")
    monkeypatch.setattr(auto, "dex_name", dex)
    strat.candidates.return_value = ([cand("A", liq=500_000, h1=10.0)], None)

    result = await auto.run_strategy_monitor(make_ctx())

    dex.assert_awaited_once_with(strat.conf, "0xpair-a", short=True)
    assert result["buys"] == ["$A · +10.0% 1h · $500k · V3 0.3%"]


@pytest.mark.asyncio
async def test_strategy_records_successful_buy(strat):
    strat.candidates.return_value = ([cand("A", liq=500_000, h1=10.0)], None)

    summary = (await auto.run_strategy_monitor(make_ctx()))["text"]

    assert "🛒 Bought: `1` (1/5 positions)" in summary
    assert "✅ $A (+10.0% / $500,000)" in summary
    entry = {
        "target_address": "0xa",
        "pair_address": "0xpair-a",
        "symbol": "A",
        "network": "Base",
        "buy_tx": "0xbuytx",
    }
    assert strat.metrics.await_args.args == (entry, strat.conf)
    strat.position_add.assert_awaited_once_with(
        entry, strat.metrics.return_value, "clear", True, strat.gp
    )
    strat.strat_add.assert_awaited_once_with("0xa", "0xpair-a", "A", "monitoring")


@pytest.mark.parametrize(
    "text, status, setup",
    [
        (
            "Honeypot (can't sell)",
            "honey-pot",
            lambda m: m.simulate.configure_mock(return_value=False),
        ),
        (
            "Honeypot (honeypot.is)",
            "honey-pot",
            lambda m: m.honeypot.configure_mock(return_value=False),
        ),
        (
            "Honeypot (honeypot.is)",
            "honey-pot",
            lambda m: m.honeypot.configure_mock(return_value=None),
        ),
        (
            "No Data",
            "no-data",
            lambda m: m.audit.configure_mock(return_value=("clear", True, None)),
        ),
        (
            "Unverified",
            "unverified",
            lambda m: m.audit.configure_mock(
                return_value=("unverified", True, {"sell_tax": 0.0})
            ),
        ),
        (
            "High Tax (25% sell tax)",
            "high-tax",
            lambda m: m.audit.configure_mock(
                return_value=("clear", True, {"sell_tax": 0.25})
            ),
        ),
        (
            "Big Holder (40% in one wallet)",
            "whale",
            lambda m: m.audit.configure_mock(
                return_value=("clear", True, {"sell_tax": 0.0, "top_holder_pct": 40})
            ),
        ),
    ],
)
@pytest.mark.asyncio
async def test_strategy_rejects_unsafe_tokens(strat, text, status, setup):
    setup(strat)
    strat.candidates.return_value = ([cand("A")], None)

    summary = (await auto.run_strategy_monitor(make_ctx()))["text"]

    strat.buy.assert_not_awaited()
    strat.position_add.assert_not_awaited()
    strat.strat_add.assert_awaited_once_with("0xa", "0xpair-a", "A", status)
    assert f"🚫 A — {text}" in summary
    assert "🛡️ Passed: `0`" in summary
    assert "🛒 Bought: `0`" in summary


@pytest.mark.asyncio
async def test_strategy_failed_buy_is_reported_and_not_recorded(strat):
    strat.buy.return_value = (None, "insufficient funds")
    strat.candidates.return_value = ([cand("A")], None)

    summary = (await auto.run_strategy_monitor(make_ctx()))["text"]

    assert "❌ A — insufficient funds" in summary
    assert "🛡️ Passed: `1`" in summary
    assert "🛒 Bought: `0`" in summary
    strat.position_add.assert_not_awaited()
    strat.strat_add.assert_not_awaited()


@pytest.mark.asyncio
async def test_strategy_one_failing_candidate_does_not_stop_the_rest(strat):
    strat.candidates.return_value = ([cand("A", h1=20), cand("B", h1=10)], None)
    strat.audit.side_effect = [RuntimeError("boom"), ("clear", True, strat.gp)]

    summary = (await auto.run_strategy_monitor(make_ctx()))["text"]

    assert "⚠️ A — boom" in summary
    assert "✅ $B" in summary
    assert "🛒 Bought: `1`" in summary
    strat.capture.assert_called_once()
    strat.strat_add.assert_awaited_once_with("0xb", "0xpair-b", "B", "monitoring")


@pytest.fixture
def auditor(monkeypatch):
    monkeypatch.setattr(auto, "OPTIONS", {"max_positions": 5})
    m = SimpleNamespace(
        count=AsyncMock(return_value=2),
        ready=AsyncMock(return_value=[{"id": 1}]),
        audit=AsyncMock(return_value="audited"),
    )
    monkeypatch.setattr(auto, "pairs_get_status_count", m.count)
    monkeypatch.setattr(auto, "pairs_get_for_audit", m.ready)
    monkeypatch.setattr(auto, "audit_pair", m.audit)
    return m


@pytest.mark.asyncio
async def test_auditor_stops_when_positions_full(auditor):
    auditor.count.return_value = 5

    result = await auto.run_pairs_monitor(make_ctx())

    assert result == "⏸️ Max positions reached (5/5)"
    auditor.ready.assert_not_awaited()
    auditor.audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_auditor_counts_monitoring_positions(auditor):
    await auto.run_pairs_monitor(make_ctx())

    auditor.count.assert_awaited_once_with("monitoring", "positions")


@pytest.mark.asyncio
async def test_auditor_nothing_ready(auditor):
    auditor.ready.return_value = []

    assert await auto.run_pairs_monitor(make_ctx()) == "📭 No pairs ready to audit"
    auditor.audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_auditor_passes_free_slots_as_max_buys(auditor):
    ctx = make_ctx()

    assert await auto.run_pairs_monitor(ctx) == "audited"

    auditor.audit.assert_awaited_once_with(ctx, [{"id": 1}], max_buys=3)


@pytest.mark.asyncio
async def test_auditor_errors_propagate(auditor):
    auditor.ready.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await auto.run_pairs_monitor(make_ctx())


BASE_CONF = {"chain_id": 8453}


@pytest.fixture
def mon(monkeypatch):
    freeze_datetime(monkeypatch, utc(2026, 10, 18, 9), module=auto)
    monkeypatch.setattr(
        auto,
        "OPTIONS",
        {
            "take_profit_percent": 50,
            "stop_loss_percent": 20,
            "rug_liq_threshold": 30,
            "trailing_stop_percent": 20,
            "trailing_start_percent": 20,
            "dead_token_hours": 1,
            "dead_token_threshold": 2.0,
        },
    )
    monkeypatch.setattr(auto, "NETWORKS", {"Base": BASE_CONF})
    monkeypatch.setattr(auto, "_NO_ENTRY_WARNED", set())

    gp = {"is_honeypot": False}
    m = SimpleNamespace(
        gp=gp,
        positions=AsyncMock(return_value=[]),
        metrics=AsyncMock(return_value={"price_eth": 1.0, "liq_eth": 10.0}),
        audit=AsyncMock(return_value=("clear", True, gp)),
        honeypot=AsyncMock(return_value=True),
        sell=AsyncMock(return_value=("0xsell", None)),
        update=AsyncMock(),
        completed=AsyncMock(),
        sleep=AsyncMock(),
        capture=MagicMock(),
    )
    monkeypatch.setattr(auto, "pairs_get_by_status", m.positions)
    monkeypatch.setattr(auto, "get_token_metrics", m.metrics)
    monkeypatch.setattr(auto, "get_security_audit", m.audit)
    monkeypatch.setattr(auto, "check_honeypot", m.honeypot)
    monkeypatch.setattr(auto, "execute_sell", m.sell)
    monkeypatch.setattr(auto, "pairs_position_update", m.update)
    monkeypatch.setattr(auto, "pairs_mark_completed", m.completed)
    monkeypatch.setattr(auto, "trade_result", AsyncMock(return_value=None))
    monkeypatch.setattr(auto, "dex_name", AsyncMock(return_value=None))
    monkeypatch.setattr(auto, "asyncio", SimpleNamespace(sleep=m.sleep))
    monkeypatch.setattr(
        auto, "sentry_sdk", SimpleNamespace(capture_exception=m.capture)
    )
    return m


def make_position(**over):
    pos = {
        "id": 7,
        "target_address": "0xtoken",
        "network": "Base",
        "buy_tx": "0xbuy",
        "entry_price_eth": 1.0,
        "entry_liq_eth": 10.0,
        "peak_pnl_percent": 0,
        "tracked_at": datetime(2026, 10, 18, 9, tzinfo=ZoneInfo("UTC")),
        "current_sec_status": "clear",
        "is_renounced": True,
        "symbol": "TKN",
    }
    pos.update(over)
    return pos


async def run_monitor(m, pos, price=1.0, liq=10.0):
    m.positions.return_value = [pos]
    m.metrics.return_value = {"price_eth": price, "liq_eth": liq}
    result = await auto.run_sell_monitor(make_ctx())
    return result["sells"] + result["losses"]


@pytest.mark.asyncio
async def test_monitor_no_positions_does_nothing(mon):
    assert await auto.run_sell_monitor(make_ctx()) == {
        "sells": [],
        "losses": [],
        "charts": [],
    }

    mon.positions.assert_awaited_once_with(status="monitoring", table="positions")
    mon.metrics.assert_not_awaited()
    mon.sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_monitor_skips_rows_that_are_not_dicts(mon):
    mon.positions.return_value = ["junk", None, make_position()]

    await auto.run_sell_monitor(make_ctx())

    mon.metrics.assert_awaited_once()


@pytest.mark.parametrize(
    "over, price, liq",
    [
        ({}, 1.5, 10.0),
        ({}, 0.8, 10.0),
        ({"peak_pnl_percent": 60}, 1.35, 10.0),
        ({"peak_pnl_percent": 40}, 1.15, 10.0),
        ({}, 1.0, 6.0),
        ({}, 1.0, 7.0),
        ({"tracked_at": utc(2026, 10, 18, 7)}, 1.0, 10.0),
        ({"tracked_at": utc(2026, 10, 18, 7)}, 1.01, 10.0),
    ],
    ids=[
        "take-profit",
        "stop-loss",
        "trailing-stop",
        "trailing-stop-before-take-profit",
        "rug",
        "rug-at-threshold",
        "dead-token",
        "dead-token-small-move",
    ],
)
@pytest.mark.asyncio
async def test_monitor_sells_and_completes_on_exit_signal(mon, over, price, liq):
    pos = make_position(**over)

    await run_monitor(mon, pos, price, liq)

    mon.sell.assert_awaited_once_with(BASE_CONF, pos)
    mon.update.assert_awaited_once()
    mon.completed.assert_awaited_once_with("0xtoken", sell_tx="0xsell")


@pytest.mark.parametrize(
    "over, price, liq",
    [
        ({}, 1.1, 10.0),
        ({}, 1.49, 10.0),
        ({}, 0.81, 10.0),
        ({"peak_pnl_percent": 60}, 1.45, 10.0),
        ({"peak_pnl_percent": 15}, 0.9, 10.0),
        ({}, 1.0, 7.1),
        ({"tracked_at": utc(2026, 10, 18, 7), "peak_pnl_percent": 5}, 1.0, 10.0),
        ({"tracked_at": utc(2026, 10, 18, 7)}, 1.02, 10.0),
        ({"tracked_at": utc(2026, 10, 18, 7)}, 1.0, 10.3),
    ],
    ids=[
        "small-gain",
        "just-under-take-profit",
        "just-above-stop-loss",
        "trailing-not-triggered",
        "peak-below-trailing-start",
        "liq-just-above-rug",
        "old-but-had-a-peak",
        "old-but-price-moved",
        "old-but-liq-moved",
    ],
)
@pytest.mark.asyncio
async def test_monitor_holds_when_no_exit_signal(mon, over, price, liq):
    await run_monitor(mon, make_position(**over), price, liq)

    mon.sell.assert_not_awaited()
    mon.completed.assert_not_awaited()
    mon.update.assert_awaited_once()
    mon.sleep.assert_awaited_once_with(0.5)


@pytest.mark.asyncio
async def test_monitor_young_flat_position_is_not_dead(mon):
    pos = make_position(tracked_at=datetime(2026, 10, 18, 8, 30, tzinfo=timezone.utc))
    await run_monitor(mon, pos)

    mon.sell.assert_not_awaited()
    mon.completed.assert_not_awaited()


@pytest.mark.asyncio
async def test_monitor_hold_updates_position_with_fresh_values(mon):
    await run_monitor(mon, make_position(), price=1.1, liq=9.0)

    args = mon.update.await_args.args
    assert args[0] == {"price_eth": 1.1, "liq_eth": 9.0}
    assert args[1] == 10.0
    assert args[2] == -10.0
    assert args[3:6] == ("clear", True, 7)
    assert args[6] is mon.gp


@pytest.mark.asyncio
async def test_monitor_naive_tracked_at_is_treated_as_utc(mon):
    pos = make_position(tracked_at=datetime(2026, 10, 18, 7, 0))

    await run_monitor(mon, pos)

    mon.sell.assert_awaited_once()
    mon.completed.assert_awaited_once_with("0xtoken", sell_tx="0xsell")


@pytest.mark.asyncio
async def test_monitor_missing_tracked_at_is_never_dead(mon):
    await run_monitor(mon, make_position(tracked_at=None))

    mon.sell.assert_not_awaited()
    mon.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_monitor_position_without_buy_tx_is_closed_flat_without_selling(mon):
    await run_monitor(mon, make_position(buy_tx=None), price=1.5)

    mon.sell.assert_not_awaited()
    mon.update.assert_awaited_once()
    mon.completed.assert_awaited_once_with("0xtoken", pnl=0)


@pytest.mark.asyncio
async def test_monitor_transfer_from_failed_closes_at_total_loss(mon):
    mon.sell.return_value = (None, "execution reverted: TRANSFER_FROM_FAILED")

    await run_monitor(mon, make_position(), price=1.5)

    mon.update.assert_awaited_once()
    mon.completed.assert_awaited_once_with("0xtoken", pnl=-100)


@pytest.mark.asyncio
async def test_monitor_failed_sell_is_retried_next_cycle(mon):
    mon.sell.return_value = (None, "slippage too high")

    await run_monitor(mon, make_position(), price=1.5)

    mon.sell.assert_awaited_once()
    mon.completed.assert_not_awaited()
    mon.update.assert_awaited_once()
    mon.sleep.assert_awaited_once_with(0.5)


@pytest.mark.asyncio
async def test_monitor_unknown_honeypot_status_keeps_position(mon):
    mon.honeypot.return_value = None

    await run_monitor(mon, make_position(), price=1.1)

    mon.completed.assert_not_awaited()
    mon.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_monitor_closes_position_with_no_tokens_left(mon):
    mon.sell.return_value = (None, "No token balance")

    sells = await run_monitor(mon, make_position(), price=1.5)

    mon.update.assert_awaited_once()
    mon.completed.assert_awaited_once_with("0xtoken", pnl=50.0)
    assert sells == ["📝 $TKN · no tokens left, closed · +50.00%"]


@pytest.mark.asyncio
async def test_monitor_pending_sell_on_rug_stays_open(mon):
    mon.sell.return_value = (None, "PENDING")

    await run_monitor(mon, make_position(), liq=5.0)

    mon.completed.assert_not_awaited()
    mon.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_monitor_failed_sell_on_rug_closes_at_total_loss(mon):
    mon.sell.return_value = (None, "slippage too high")

    await run_monitor(mon, make_position(), liq=5.0)

    mon.update.assert_awaited_once()
    mon.completed.assert_awaited_once_with("0xtoken", pnl=-100)


@pytest.mark.parametrize(
    "setup",
    [
        lambda m: m.honeypot.configure_mock(return_value=False),
        lambda m: m.gp.update(is_honeypot=True),
    ],
    ids=["honeypot-check-fails", "audit-flags-honeypot"],
)
@pytest.mark.asyncio
async def test_monitor_honeypot_tries_to_sell_first(mon, setup):
    setup(mon)

    sells = await run_monitor(mon, make_position(), price=1.1)

    mon.sell.assert_awaited_once()
    mon.update.assert_awaited_once()
    mon.completed.assert_awaited_once_with("0xtoken", sell_tx="0xsell")
    assert sells == ["☠️ $TKN · honeypot flagged, sold · +10.00%"]


@pytest.mark.asyncio
async def test_monitor_honeypot_unsellable_closes_at_total_loss(mon):
    mon.honeypot.return_value = False
    mon.sell.return_value = (None, "execution reverted")

    sells = await run_monitor(mon, make_position(), price=1.1)

    mon.completed.assert_awaited_once_with("0xtoken", pnl=-100)
    assert sells == ["☠️ $TKN · honeypot detected · -100%"]


@pytest.mark.asyncio
async def test_monitor_honeypot_pending_sell_stays_open(mon):
    mon.honeypot.return_value = False
    mon.sell.return_value = (None, "PENDING")

    assert await run_monitor(mon, make_position(), price=1.1) == []
    mon.completed.assert_not_awaited()


@pytest.mark.asyncio
async def test_monitor_honeypot_with_no_tokens_left_closes_at_pnl(mon):
    mon.honeypot.return_value = False
    mon.sell.return_value = (None, "No token balance")

    await run_monitor(mon, make_position(), price=1.1)

    mon.completed.assert_awaited_once_with("0xtoken", pnl=10.0)


@pytest.mark.asyncio
async def test_monitor_honeypot_without_buy_tx_is_not_sold(mon):
    mon.honeypot.return_value = False

    await run_monitor(mon, make_position(buy_tx=None), price=1.1)

    mon.sell.assert_not_awaited()
    mon.completed.assert_awaited_once_with("0xtoken", pnl=-100)


@pytest.mark.asyncio
async def test_monitor_ignores_honeypot_flag_if_it_was_one_at_entry(mon):
    mon.honeypot.return_value = False

    await run_monitor(mon, make_position(entry_sec_status="honey-pot"), price=1.1)

    mon.completed.assert_not_awaited()
    mon.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_monitor_security_failure_falls_back_to_stored_status(mon):
    mon.audit.side_effect = RuntimeError("goplus down")

    await run_monitor(mon, make_position(), price=1.1)

    mon.capture.assert_called_once()
    mon.completed.assert_not_awaited()
    args = mon.update.await_args.args
    assert args[3:6] == ("clear", True, 7)
    assert args[6] is None


@pytest.mark.parametrize(
    "over",
    [{"entry_price_eth": 0}, {"entry_liq_eth": 0}, {"entry_price_eth": -1}],
)
@pytest.mark.asyncio
async def test_monitor_skips_positions_with_invalid_entry(mon, over):
    await run_monitor(mon, make_position(**over))

    mon.update.assert_not_awaited()
    mon.sell.assert_not_awaited()
    mon.completed.assert_not_awaited()


@pytest.mark.asyncio
async def test_monitor_reports_unparseable_entry_values(mon):
    await run_monitor(mon, make_position(entry_price_eth=None))

    mon.capture.assert_called_once()
    mon.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_monitor_one_failing_position_does_not_stop_the_rest(mon):
    mon.positions.return_value = [make_position(id=1), make_position(id=2)]
    mon.metrics.side_effect = [
        RuntimeError("rpc down"),
        {"price_eth": 1.0, "liq_eth": 10.0},
    ]

    await auto.run_sell_monitor(make_ctx())

    mon.capture.assert_called_once()
    mon.update.assert_awaited_once()
    assert mon.update.await_args.args[5] == 2


@pytest.mark.parametrize(
    "over, price, liq, expected",
    [
        ({}, 1.5, 10.0, "$TKN · take-profit · +50.00%"),
        ({}, 0.8, 10.0, "$TKN · stop-loss · -20.00%"),
        (
            {"peak_pnl_percent": 60},
            1.35,
            10.0,
            "$TKN · trailing-stop · +35.00%",
        ),
        ({}, 1.0, 6.0, "$TKN · rug · +0.00%"),
        (
            {"tracked_at": utc(2026, 10, 18, 7)},
            1.0,
            10.0,
            "$TKN · dead token · +0.00%",
        ),
    ],
    ids=["take-profit", "stop-loss", "trailing-stop", "rug", "dead-token"],
)
@pytest.mark.asyncio
async def test_monitor_reports_each_sale_with_reason_and_pnl(
    mon, over, price, liq, expected
):
    assert await run_monitor(mon, make_position(**over), price, liq) == [expected]


@pytest.mark.asyncio
async def test_monitor_records_and_reports_real_pnl(mon, monkeypatch):
    real = {"eth_spent": 0.0000108, "eth_received": 0.0000081, "pnl": -25.0}
    trade = AsyncMock(return_value=real)
    monkeypatch.setattr(auto, "trade_result", trade)

    closed = await run_monitor(mon, make_position(), price=1.5)

    trade.assert_awaited_once_with(BASE_CONF, "0xbuy", "0xsell", earlier_eth=None)
    mon.completed.assert_awaited_once_with("0xtoken", sell_tx="0xsell", **real)
    assert closed == ["$TKN · take-profit · -25.00%"]


@pytest.mark.asyncio
async def test_sell_all_records_real_result_for_open_positions(seller, monkeypatch):
    real = {"eth_spent": 0.0000108, "eth_received": 0.0000081, "pnl": -25.0}
    monkeypatch.setattr(auto, "trade_result", AsyncMock(return_value=real))
    seller.positions.return_value = [held("OPEN", "0xopen", "monitoring")]

    await auto.run_sell_all(SimpleNamespace())

    seller.completed.assert_awaited_once_with("0xopen", sell_tx="0xsell", **real)


@pytest.mark.asyncio
async def test_monitor_returns_charts_and_positions_left(mon):
    mon.positions.return_value = [
        make_position(id=1, symbol="AAA", pair_address="0xpair-a"),
        make_position(id=2, symbol="BBB", pair_address="0xpair-b"),
    ]
    mon.metrics.side_effect = [
        {"price_eth": 1.5, "liq_eth": 10.0},
        {"price_eth": 1.1, "liq_eth": 10.0},
    ]

    result = await auto.run_sell_monitor(make_ctx())

    assert result["charts"] == [("AAA", "0xpair-a")]
    assert result["positions"] == (1, 5)


@pytest.mark.asyncio
async def test_monitor_sell_line_shows_the_dex(mon, monkeypatch):
    monkeypatch.setattr(auto, "dex_name", AsyncMock(return_value="V2"))

    closed = await run_monitor(mon, make_position(pair_address="0xpair"), price=1.5)

    assert closed == ["$TKN · take-profit · +50.00% · V2"]


@pytest.mark.asyncio
async def test_monitor_reports_nothing_when_holding(mon):
    assert await run_monitor(mon, make_position(), price=1.1) == []


@pytest.mark.asyncio
async def test_monitor_reports_honeypot_close(mon):
    mon.honeypot.return_value = False
    mon.sell.return_value = (None, "execution reverted")

    closed = await run_monitor(mon, make_position(), price=1.1)

    assert closed == ["☠️ $TKN · honeypot detected · -100%"]


@pytest.mark.asyncio
async def test_monitor_reports_unsellable_close(mon):
    mon.sell.return_value = (None, "execution reverted: TRANSFER_FROM_FAILED")

    closed = await run_monitor(mon, make_position(), price=1.5)

    assert closed == ["💀 $TKN · unsellable · -100%"]


@pytest.mark.asyncio
async def test_monitor_reports_failed_sell_on_rug(mon):
    mon.sell.return_value = (None, "slippage too high")

    closed = await run_monitor(mon, make_position(), liq=5.0)

    assert closed == ["🚨 $TKN · rug, sell failed · -100%"]


@pytest.mark.asyncio
async def test_monitor_failed_sell_is_not_reported_until_it_closes(mon):
    mon.sell.return_value = (None, "slippage too high")

    assert await run_monitor(mon, make_position(), price=1.5) == []


@pytest.mark.asyncio
async def test_monitor_reports_position_without_buy_tx(mon):
    closed = await run_monitor(mon, make_position(buy_tx=None), price=1.5)

    assert closed == ["📝 $TKN · take-profit, no buy tx · 0%"]


@pytest.mark.asyncio
async def test_monitor_missing_symbol_shows_placeholder(mon):
    pos = make_position()
    pos.pop("symbol")

    assert await run_monitor(mon, pos, price=1.5) == ["$??? · take-profit · +50.00%"]


@pytest.mark.asyncio
async def test_monitor_reports_several_closes_in_one_run(mon):
    mon.positions.return_value = [
        make_position(id=1, symbol="AAA"),
        make_position(id=2, symbol="BBB"),
    ]
    mon.metrics.side_effect = [
        {"price_eth": 1.5, "liq_eth": 10.0},
        {"price_eth": 0.8, "liq_eth": 10.0},
    ]

    result = await auto.run_sell_monitor(make_ctx())

    assert result["sells"] == ["$AAA · take-profit · +50.00%"]
    assert result["losses"] == ["$BBB · stop-loss · -20.00%"]


@pytest.mark.asyncio
async def test_strategy_escapes_symbol_in_buy_line(strat):
    strat.candidates.return_value = ([cand("MY_TOKEN", liq=500_000, h1=10.0)], None)

    summary = (await auto.run_strategy_monitor(make_ctx()))["text"]

    assert r"✅ $MY\_TOKEN (+10.0% / $500,000)" in summary
    strat.strat_add.assert_awaited_once_with(
        "0xmy_token", "0xpair-my_token", "MY_TOKEN", "monitoring"
    )


@pytest.mark.asyncio
async def test_strategy_escapes_failed_buy_error(strat):
    strat.buy.return_value = (None, "execution reverted: TRANSFER_FROM_FAILED")
    strat.candidates.return_value = ([cand("A")], None)

    summary = (await auto.run_strategy_monitor(make_ctx()))["text"]

    assert r"❌ A — execution reverted: TRANSFER\_FROM\_FAILED" in summary


@pytest.mark.asyncio
async def test_strategy_escapes_rejection_reason(strat):
    strat.audit.return_value = ("is_proxy", True, {"sell_tax": 0.0})
    strat.candidates.return_value = ([cand("A")], None)

    summary = (await auto.run_strategy_monitor(make_ctx()))["text"]

    assert r"🚫 A — is\_proxy" in summary


@pytest.mark.asyncio
async def test_monitor_escapes_symbol(mon):
    closed = await run_monitor(mon, make_position(symbol="MY_TOKEN"), price=1.5)

    assert closed == [r"$MY\_TOKEN · take-profit · +50.00%"]


@pytest.fixture
def seller(monkeypatch):
    monkeypatch.setattr(auto, "NETWORKS", {"Base": BASE_CONF})
    m = SimpleNamespace(
        positions=AsyncMock(return_value=[]),
        sell=AsyncMock(return_value=("0xsell", None)),
        completed=AsyncMock(),
        capture=MagicMock(),
    )
    monkeypatch.setattr(auto, "pairs_get_by_status", m.positions)
    monkeypatch.setattr(auto, "execute_sell", m.sell)
    monkeypatch.setattr(auto, "pairs_mark_completed", m.completed)
    monkeypatch.setattr(auto, "trade_result", AsyncMock(return_value=None))
    monkeypatch.setattr(
        auto, "sentry_sdk", SimpleNamespace(capture_exception=m.capture)
    )
    return m


def held(symbol, addr, status="completed"):
    return {
        "symbol": symbol,
        "target_address": addr,
        "network": "Base",
        "status": status,
    }


@pytest.mark.asyncio
async def test_sell_all_looks_at_every_position(seller):
    await auto.run_sell_all(SimpleNamespace())

    seller.positions.assert_awaited_once_with(status=None, table="positions")


@pytest.mark.asyncio
async def test_sell_all_nothing_to_sell(seller):
    assert await auto.run_sell_all(SimpleNamespace()) == (
        "💼 *Sell All*\n\nNothing left in the wallet to sell."
    )


@pytest.mark.asyncio
async def test_sell_all_sells_each_token_once_and_skips_dust(seller):
    seller.positions.return_value = [
        held("AAA", "0xa", "monitoring"),
        held("AAA", "0xA"),
        held("BBB", "0xb"),
        held("CCC", "0xc"),
    ]
    seller.sell.side_effect = [
        ("0xsell", None),
        (None, "dust"),
        (None, "No token balance"),
    ]

    result = await auto.run_sell_all(SimpleNamespace())

    assert seller.sell.await_count == 3
    for call in seller.sell.await_args_list:
        assert call.kwargs == {"skip_dust": True}
    assert result == (
        "💼 *Sell All*\n\n✅ $AAA sold\n🧹 $BBB — worth less than the gas"
    )


@pytest.mark.asyncio
async def test_sell_all_only_completes_open_positions(seller):
    seller.positions.return_value = [
        held("OPEN", "0xopen", "monitoring"),
        held("DONE", "0xdone"),
    ]

    await auto.run_sell_all(SimpleNamespace())

    seller.completed.assert_awaited_once_with("0xopen", sell_tx="0xsell")


@pytest.mark.asyncio
async def test_sell_all_reports_failures_and_carries_on(seller):
    seller.positions.return_value = [
        held("MY_TOKEN", "0xa"),
        held("BBB", "0xb"),
        held("CCC", "0xc"),
    ]
    seller.sell.side_effect = [
        (None, "SELL_REVERTED"),
        RuntimeError("rpc down"),
        ("0xsell", None),
    ]

    result = await auto.run_sell_all(SimpleNamespace())

    seller.capture.assert_called_once()
    assert "✅ $CCC sold" in result
    assert r"❌ $MY\_TOKEN — SELL\_REVERTED" in result
    assert "❌ $BBB — rpc down" in result


@pytest.mark.asyncio
async def test_monitor_warns_once_about_position_with_no_entry_price(mon):
    pos = make_position(entry_price_eth=0)

    first = await run_monitor(mon, pos)
    second = await run_monitor(mon, pos)

    assert first == ["⚠️ $TKN · no entry price, not monitored — sell or remove it"]
    assert second == []
    mon.sell.assert_not_awaited()
    mon.completed.assert_not_awaited()


@pytest.mark.asyncio
async def test_monitor_warns_separately_for_each_position(mon):
    mon.positions.return_value = [
        make_position(id=1, symbol="AAA", entry_liq_eth=0),
        make_position(id=2, symbol="BBB", entry_price_eth=0),
    ]

    closed = (await auto.run_sell_monitor(make_ctx()))["sells"]

    assert [line.split(" ·")[0] for line in closed] == ["⚠️ $AAA", "⚠️ $BBB"]


@pytest.fixture
def halves(mon, monkeypatch):
    auto.OPTIONS["half_sell_percent"] = 30
    trade = AsyncMock(
        return_value={"eth_spent": 0.00001, "eth_received": 0.0000065, "pnl": -35.0}
    )
    monkeypatch.setattr(auto, "trade_result", trade)
    mon.trade = trade
    return mon


@pytest.mark.asyncio
async def test_half_is_sold_once_the_gain_is_reached(halves):
    pos = make_position()

    sells = await run_monitor(halves, pos, price=1.35)

    halves.sell.assert_awaited_once_with(BASE_CONF, pos, half=True)
    assert halves.update.await_args.kwargs == {
        "half_eth": 0.0000065,
        "eth_spent": 0.00001,
    }
    halves.completed.assert_not_awaited()
    assert sells == ["$TKN · sold half · +35.00%"]


@pytest.mark.asyncio
async def test_take_profit_still_sells_everything(halves):
    pos = make_position()

    await run_monitor(halves, pos, price=1.6)

    halves.sell.assert_awaited_once_with(BASE_CONF, pos)
    halves.completed.assert_awaited_once()


@pytest.mark.asyncio
async def test_half_is_only_sold_once(halves):
    await run_monitor(
        halves, make_position(half_eth=0.0000065, peak_pnl_percent=35), price=1.35
    )

    halves.sell.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_rest_sells_at_break_even_and_counts_both_halves(halves):
    pos = make_position(half_eth=Decimal("0.0000065"), peak_pnl_percent=35)

    sells = await run_monitor(halves, pos, price=0.99)

    halves.sell.assert_awaited_once_with(BASE_CONF, pos)
    halves.trade.assert_awaited_once_with(
        BASE_CONF, "0xbuy", "0xsell", earlier_eth=Decimal("0.0000065")
    )
    assert sells == ["$TKN · break-even · -35.00%"]


@pytest.mark.asyncio
async def test_a_rug_after_half_keeps_what_the_half_brought_back(halves):
    halves.sell.return_value = (None, "execution reverted")
    pos = make_position(
        half_eth=Decimal("0.0000065"), eth_spent=Decimal("0.00001"), peak_pnl_percent=35
    )

    sells = await run_monitor(halves, pos, price=1.0, liq=1.0)

    halves.trade.assert_awaited_once_with(
        BASE_CONF, "0xbuy", earlier_eth=Decimal("0.0000065")
    )
    halves.completed.assert_awaited_once_with(
        "0xtoken", eth_spent=0.00001, eth_received=0.0000065, pnl=-35.0
    )
    assert sells == ["🚨 $TKN · rug, sell failed · -35%"]


@pytest.mark.asyncio
async def test_a_trade_that_cannot_be_sold_still_counts_what_it_cost(mon, monkeypatch):
    cost = {"eth_spent": 0.00001, "eth_received": 0, "pnl": -100.0}
    monkeypatch.setattr(auto, "trade_result", AsyncMock(return_value=cost))
    mon.sell.return_value = (None, "execution reverted")

    sells = await run_monitor(mon, make_position(), price=1.0, liq=1.0)

    mon.completed.assert_awaited_once_with("0xtoken", **cost)
    assert sells == ["🚨 $TKN · rug, sell failed · -100%"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "created_at,min_age,bought",
    [
        ("2024-01-01T00:00:00Z", 24, True),
        (None, 24, False),
        ("young", 24, False),
        ("young", 0, True),
    ],
    ids=["old-pool", "unknown-age", "young-pool", "age-check-off"],
)
async def test_strategy_skips_pools_younger_than_the_minimum_age(
    strat, created_at, min_age, bought
):
    if created_at == "young":
        created_at = (datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat()
    strat.settings["strat_min_pool_age_hours"] = min_age
    strat.candidates.return_value = ([cand("A", created_at=created_at)], None)

    await auto.run_strategy_monitor(make_ctx())

    assert strat.buy.await_count == int(bought)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pool,status,text",
    [
        ({"success": False}, "metrics-rejected", "pool can't be read"),
        ({"success": True, "liq_eth": 0.0026}, "low-liquidity", "$16 in the pool"),
    ],
)
async def test_strategy_checks_the_pool_on_chain_before_buying(
    strat, pool, status, text
):
    strat.metrics.return_value = pool
    strat.candidates.return_value = ([cand("A")], None)

    summary = (await auto.run_strategy_monitor(make_ctx()))["text"]

    strat.buy.assert_not_awaited()
    strat.strat_add.assert_awaited_once_with("0xa", "0xpair-a", "A", status)
    assert text in summary


@pytest.mark.asyncio
async def test_strategy_records_each_run_for_the_health_check(strat):
    strat.candidates.return_value = (
        [cand("A"), cand("B")],
        "fetch failed (status 429)",
    )

    await auto.run_strategy_monitor(make_ctx())

    assert auto.STRATEGY_RUN["fetched"] == 2
    assert auto.STRATEGY_RUN["error"] == "fetch failed (status 429)"

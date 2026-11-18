from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import pair_scanner.jobs as jobs
from pair_scanner.settings import OPTIONS


def ctx(name, jq=None):
    return SimpleNamespace(
        job=SimpleNamespace(name=name),
        bot=SimpleNamespace(send_message=AsyncMock()),
        application=SimpleNamespace(job_queue=jq, bot_data={}),
    )


@pytest.mark.asyncio
async def test_buy_alert_lists_buys_watched_and_failures(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "run_pairs_monitor",
        AsyncMock(
            return_value={
                "buys": ["$AAA · 2.00 ETH liquidity"],
                "watched": ["$BBB · 3.00 ETH liquidity"],
                "failed": ["❌ CCC — reverted"],
                "charts": [("AAA", "0xpair")],
                "positions": (2, 3),
            }
        ),
    )
    c = ctx("Pairs Monitor")

    await jobs.send_buys(c)

    sent = c.bot.send_message.await_args.kwargs
    assert sent["chat_id"] == "-100"
    assert sent["text"] == (
        "🛒 *Pairs Buy*\n └ $AAA · 2.00 ETH liquidity\n\n"
        "👀 *Pairs: would buy* (watch-only)\n └ $BBB · 3.00 ETH liquidity\n\n"
        "⚠️ *Pairs Buy Failed*\n └ ❌ CCC — reverted\n\n"
        "💼 Positions: 2/3"
    )
    buttons = [b for row in sent["reply_markup"].inline_keyboard for b in row]
    assert buttons[0].url == "https://dexscreener.com/base/0xpair"
    assert buttons[-1].callback_data == "scanner:close"


@pytest.mark.asyncio
async def test_watch_only_alerts_have_no_positions_line(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "run_strategy_monitor",
        AsyncMock(return_value={"watched": ["$BBB"], "positions": (0, 3)}),
    )
    c = ctx("Strategy Monitor")

    await jobs.send_buys(c)

    text = c.bot.send_message.await_args.kwargs["text"]
    assert text.startswith("👀 *Strategy: would buy*")
    assert "Positions" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result,alerts_on",
    [("📭 No pairs ready to audit", 1), ({}, 1), ({"buys": ["$A"]}, 0)],
    ids=["nothing-to-audit", "empty", "alerts-off"],
)
async def test_quiet_runs_send_nothing(monkeypatch, result, alerts_on):
    OPTIONS["buy_alerts_enabled"] = alerts_on
    monkeypatch.setattr(jobs, "run_pairs_monitor", AsyncMock(return_value=result))
    c = ctx("Pairs Monitor")

    await jobs.send_buys(c)

    c.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_sell_alert(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "run_sell_monitor",
        AsyncMock(
            return_value={
                "sells": ["$A · take-profit · +60.00%", "$B · stop-loss · -21.00%"],
                "charts": [],
                "positions": (1, 3),
            }
        ),
    )
    c = ctx("Sell Monitor")

    await jobs.send_sells(c)

    assert c.bot.send_message.await_args.kwargs["text"] == (
        "💰 *Sell*\n ├ $A · take-profit · +60.00%\n └ $B · stop-loss · -21.00%"
        "\n\n💼 Positions: 1/3"
    )


@pytest.mark.asyncio
async def test_low_eth_switches_the_monitors_off_and_alerts(monkeypatch, jq):
    OPTIONS.update(pairs_enabled=1, strategy_enabled=1)
    monkeypatch.setattr(
        jobs,
        "scanner_health",
        AsyncMock(return_value={"alerts": ["⚠️ *LOW ETH BALANCE*"], "low_eth": True}),
    )
    saved = []

    async def option_set(name, value):
        saved.append((name, value))
        OPTIONS[name] = value

    monkeypatch.setattr(jobs, "option_set", option_set)
    for name in ("Pairs Monitor", "Strategy Monitor", "Sell Monitor"):
        jq.add_existing(name)
    c = ctx("Scanner Health", jq)

    await jobs.send_health(c)

    assert saved == [("pairs_enabled", 0), ("strategy_enabled", 0)]
    assert jq.get_jobs_by_name("Pairs Monitor") == []
    assert jq.get_jobs_by_name("Strategy Monitor") == []
    assert jq.get_jobs_by_name("Sell Monitor") == []
    text = c.bot.send_message.await_args.kwargs["text"]
    assert "LOW ETH BALANCE" in text and "Scanner & Strategy Disabled" in text


@pytest.mark.asyncio
async def test_healthy_runs_send_nothing(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "scanner_health",
        AsyncMock(return_value={"alerts": [], "low_eth": False}),
    )
    c = ctx("Scanner Health")

    await jobs.send_health(c)

    c.bot.send_message.assert_not_awaited()


def test_start_schedules_only_what_is_switched_on(jq):
    OPTIONS.update(pairs_enabled=0, strategy_enabled=1, strategy_monitor_interval=30)
    app = SimpleNamespace(job_queue=jq, bot_data={})

    jobs.start_jobs(app)

    names = [c.name for c in jq.calls]
    assert names == ["Sell Monitor", "Strategy Monitor", "Scanner Health"]
    assert jq.get("Strategy Monitor").interval == 30 * 60
    assert app.bot_data["scanner_bg_tasks"] == set()


def test_changing_an_interval_reschedules_its_job(jq):
    app = SimpleNamespace(job_queue=jq, bot_data={})
    old = jq.add_existing("Pairs Monitor")

    assert jobs.apply_interval(app, "pairs_monitor_interval", 10) == (
        "Pairs Monitor",
        True,
    )
    assert old.removed
    assert jq.get("Pairs Monitor").interval == 600


def test_job_status_when_switched_off(jq):
    OPTIONS["pairs_enabled"] = 0
    app = SimpleNamespace(job_queue=jq)

    assert jobs.job_status(app, "Pairs Monitor") == ("⏸️ Disabled", "--")
    assert jobs.job_status(None, "Scanner Health") == ("Not Scheduled", "--")

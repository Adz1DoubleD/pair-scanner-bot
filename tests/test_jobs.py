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

    await jobs._send_buys(c)

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
    assert buttons[-1].callback_data == "cancel"


@pytest.mark.asyncio
async def test_watch_only_alerts_have_no_positions_line(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "run_strategy_monitor",
        AsyncMock(return_value={"watched": ["$BBB"], "positions": (0, 3)}),
    )
    c = ctx("Strategy Monitor")

    await jobs._send_buys(c)

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

    await jobs._send_buys(c)

    c.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_sell_alert(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "run_sell_monitor",
        AsyncMock(
            return_value={
                "sells": ["$A · take-profit · +60.00%"],
                "losses": ["$B · stop-loss · -21.00%"],
                "charts": [],
                "positions": (1, 3),
            }
        ),
    )
    c = ctx("Sell Monitor")

    await jobs._send_sells(c)

    assert c.bot.send_message.await_args.kwargs["text"] == (
        "💰 *Sell*\n └ $A · take-profit · +60.00%\n\n"
        "🚨 *Lost*\n └ $B · stop-loss · -21.00%"
        "\n\n💼 Positions: 1/3"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profit,loss,expected",
    [
        (0, 1, "🚨 *Lost*\n └ $B · rug · -40.00%"),
        (1, 0, "💰 *Sell*\n └ $A · sold half · +31.00%"),
        (0, 0, None),
    ],
)
async def test_profit_and_loss_alerts_switch_off_separately(
    monkeypatch, profit, loss, expected
):
    OPTIONS.update(profit_alerts_enabled=profit, loss_alerts_enabled=loss)
    monkeypatch.setattr(
        jobs,
        "run_sell_monitor",
        AsyncMock(
            return_value={
                "sells": ["$A · sold half · +31.00%"],
                "losses": ["$B · rug · -40.00%"],
                "charts": [],
            }
        ),
    )
    c = ctx("Sell Monitor")

    await jobs._send_sells(c)

    if expected is None:
        c.bot.send_message.assert_not_awaited()
    else:
        assert c.bot.send_message.await_args.kwargs["text"] == expected


@pytest.mark.asyncio
async def test_with_buy_alerts_off_would_buys_and_failures_still_arrive(monkeypatch):
    OPTIONS["buy_alerts_enabled"] = 0
    monkeypatch.setattr(
        jobs,
        "run_pairs_monitor",
        AsyncMock(
            return_value={
                "buys": ["$AAA"],
                "watched": ["$BBB"],
                "failed": ["❌ CCC — reverted"],
                "charts": [("AAA", "0xpair")],
                "positions": (2, 3),
            }
        ),
    )
    c = ctx("Pairs Monitor")

    await jobs._send_buys(c)

    sent = c.bot.send_message.await_args.kwargs
    assert sent["text"] == (
        "👀 *Pairs: would buy* (watch-only)\n └ $BBB\n\n"
        "⚠️ *Pairs Buy Failed*\n └ ❌ CCC — reverted"
    )
    assert [
        b.callback_data for row in sent["reply_markup"].inline_keyboard for b in row
    ] == ["cancel"]


@pytest.mark.asyncio
async def test_low_eth_switches_live_monitors_to_watch_and_alerts(monkeypatch, jq):
    OPTIONS.update(pairs_mode=2, strategy_mode=0)
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

    await jobs._send_health(c)

    assert saved == [("pairs_mode", 1)]
    assert len(jq.get_jobs_by_name("Pairs Monitor")) == 1
    assert jq.get_jobs_by_name("Strategy Monitor") == []
    assert len(jq.get_jobs_by_name("Sell Monitor")) == 1
    text = c.bot.send_message.await_args.kwargs["text"]
    assert "LOW ETH BALANCE" in text and "Switched to Watch" in text


@pytest.fixture(autouse=True)
def small_database(monkeypatch):
    monkeypatch.setattr(jobs, "db_total_mb", AsyncMock(return_value=1.0))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sizes,cleaned",
    [([4.5, 1.2], True), ([3.9], False)],
    ids=["over-the-limit", "under-the-limit"],
)
async def test_the_health_job_cleans_the_database_near_its_limit(
    monkeypatch, sizes, cleaned
):
    monkeypatch.setattr(jobs, "db_total_mb", AsyncMock(side_effect=sizes))
    clear = AsyncMock()
    monkeypatch.setattr(jobs, "clear_archive", clear)
    monkeypatch.setattr(
        jobs, "scanner_health", AsyncMock(return_value={"alerts": [], "low_eth": False})
    )
    c = ctx("Scanner Health")

    await jobs._send_health(c)

    assert clear.await_count == int(cleaned)
    sent = c.bot.send_message.await_args
    assert (
        sent is not None
        and "🧹 *Database auto-cleaned* — 4.50 → 1.20 MB" in sent.kwargs["text"]
    ) is cleaned


@pytest.mark.asyncio
async def test_a_failed_clean_goes_to_sentry_not_the_alerts(monkeypatch):
    monkeypatch.setattr(
        jobs, "db_total_mb", AsyncMock(side_effect=RuntimeError("db down"))
    )
    reported = []
    monkeypatch.setattr(jobs.sentry_sdk, "capture_exception", reported.append)
    monkeypatch.setattr(
        jobs, "scanner_health", AsyncMock(return_value={"alerts": [], "low_eth": False})
    )
    c = ctx("Scanner Health")

    await jobs._send_health(c)

    c.bot.send_message.assert_not_awaited()
    assert str(reported[0]) == "db down"


@pytest.mark.asyncio
async def test_healthy_runs_send_nothing(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "scanner_health",
        AsyncMock(return_value={"alerts": [], "low_eth": False}),
    )
    c = ctx("Scanner Health")

    await jobs._send_health(c)

    c.bot.send_message.assert_not_awaited()


def test_start_schedules_only_what_is_switched_on(jq):
    OPTIONS.update(pairs_mode=0, strategy_mode=1, strategy_monitor_interval=30)
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
    OPTIONS["pairs_mode"] = 0
    app = SimpleNamespace(job_queue=jq)

    assert jobs.job_status(app, "Pairs Monitor") == ("⏸️ Off", "--")
    assert jobs.job_status(None, "Scanner Health") == ("Not Scheduled", "--")


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [None, "https://dash.example/db"])
async def test_a_database_alert_links_to_the_dashboard_when_set(monkeypatch, url):
    from pair_scanner.settings import SETTINGS

    monkeypatch.setattr(SETTINGS, "db_dashboard_url", url)
    monkeypatch.setattr(
        jobs,
        "scanner_health",
        AsyncMock(
            return_value={
                "alerts": ["🗄️ *Database*: read-only"],
                "low_eth": False,
                "db_problem": True,
            }
        ),
    )
    c = ctx("Scanner Health")

    await jobs._send_health(c)

    rows = c.bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard
    urls = [b.url for row in rows for b in row if b.url]
    assert urls == ([url] if url else [])
    assert rows[-1][0].callback_data == "cancel"


def health_report(alerts):
    return AsyncMock(return_value={"alerts": alerts, "low_eth": False})


@pytest.mark.asyncio
async def test_health_alerts_once_then_says_when_it_is_fixed(monkeypatch):
    rpc = "🔥 *Base RPC*: all down"

    monkeypatch.setattr(jobs, "scanner_health", health_report([rpc]))
    first = ctx("Scanner Health")
    await jobs._send_health(first)
    assert first.bot.send_message.await_args.kwargs["text"] == (
        "🚨 *SCANNER HEALTH ALERT*\n\n• " + rpc
    )

    again = ctx("Scanner Health")
    await jobs._send_health(again)
    again.bot.send_message.assert_not_awaited()

    monkeypatch.setattr(jobs, "scanner_health", health_report([]))
    fixed = ctx("Scanner Health")
    await jobs._send_health(fixed)
    text = fixed.bot.send_message.await_args.kwargs["text"]
    assert text.startswith("✅ *SCANNER HEALTH RECOVERED*")
    assert "🔥 *Base RPC* is fine again" in text


@pytest.mark.asyncio
async def test_a_long_problem_gets_one_reminder(monkeypatch):
    rpc = "🔥 *Base RPC*: all down"
    monkeypatch.setattr(jobs, "scanner_health", health_report([rpc]))
    await jobs._send_health(ctx("Scanner Health"))
    jobs.REPORTED["🔥 *Base RPC*"]["since"] -= jobs.REMIND_AFTER

    later = ctx("Scanner Health")
    await jobs._send_health(later)
    assert later.bot.send_message.await_args.kwargs["text"].startswith(
        "⏰ *STILL A PROBLEM*"
    )

    once = ctx("Scanner Health")
    await jobs._send_health(once)
    once.bot.send_message.assert_not_awaited()


def test_both_off_schedules_nothing(jq):
    OPTIONS.update(pairs_mode=0, strategy_mode=0)
    app = SimpleNamespace(job_queue=jq, bot_data={})
    jobs.start_jobs(app)
    assert jq.calls == []


def test_changing_a_mode_only_touches_the_jobs_it_affects(jq):
    OPTIONS.update(pairs_mode=0, strategy_mode=1)
    app = SimpleNamespace(job_queue=jq, bot_data={})
    sell = jq.add_existing("Sell Monitor")
    strategy = jq.add_existing("Strategy Monitor")
    jq.add_existing("Scanner Health")

    OPTIONS["strategy_mode"] = 2
    jobs.apply_modes(app)
    assert not sell.removed and not strategy.removed

    OPTIONS["strategy_mode"] = 0
    jobs.apply_modes(app)
    assert sell.removed and strategy.removed


@pytest.mark.asyncio
async def test_watch_results_are_sent_even_with_sell_alerts_off(monkeypatch):
    OPTIONS.update(profit_alerts_enabled=0, loss_alerts_enabled=0)
    monkeypatch.setattr(
        jobs,
        "run_sell_monitor",
        AsyncMock(
            return_value={
                "sells": ["$A · take-profit · +60.00%"],
                "losses": [],
                "paper": ["$W · would sell half · +31.00%"],
                "charts": [],
                "positions": (0, 3),
            }
        ),
    )
    c = ctx("Sell Monitor")

    await jobs._send_sells(c)

    assert c.bot.send_message.await_args.kwargs["text"] == (
        "👀 *Would have sold* (watch-only)\n └ $W · would sell half · +31.00%"
    )

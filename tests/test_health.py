from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

import pair_scanner.health as health
from pair_scanner.settings import OPTIONS


@pytest.fixture
def database(monkeypatch, make_cursor, patch_session):
    def setup(mb=1.0, grants="GRANT ALL PRIVILEGES ON `db`.* TO `u`", error=None):
        monkeypatch.setattr(
            health, "db_total_mb", AsyncMock(return_value=mb, side_effect=error)
        )
        patch_session(health, make_cursor(fetchall=[[{"Grants": grants}]]))

    setup()
    return setup


@pytest.fixture
def chain(monkeypatch, database):
    monkeypatch.setattr(health, "pairs_get_by_status", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        health, "STARTED_AT", datetime.now(timezone.utc) - timedelta(days=1)
    )
    monkeypatch.setattr(
        health,
        "STRATEGY_RUN",
        {"at": datetime.now(timezone.utc), "fetched": 120, "error": None},
    )

    def setup(rpcs=((["A 🟢 5ms"], []),), balances=(0.5,), stats=None):
        monkeypatch.setattr(
            health, "NETWORKS", {f"Net{i}": {} for i in range(len(balances))}
        )
        monkeypatch.setattr(
            health,
            "get_w3_service",
            AsyncMock(side_effect=list(rpcs) * len(balances)),
        )
        monkeypatch.setattr(
            health,
            "fetch_network_bal",
            AsyncMock(side_effect=[(f"Net{i}", b) for i, b in enumerate(balances)]),
        )
        found = datetime.now(timezone.utc) - timedelta(minutes=5)
        monkeypatch.setattr(
            health,
            "pairs_get_stats",
            AsyncMock(
                return_value=stats or ({"awaiting": 0}, None, None, {"found_at": found})
            ),
        )

    return setup


@pytest.mark.asyncio
async def test_all_well(chain):
    chain()

    report = await health.scanner_health()

    assert report["alerts"] == []
    assert report["low_eth"] is False
    assert report["eth"] == 0.5
    assert "Net0: 🟢 1/1 up" in report["text"]
    assert "Pairs: 💰 Live" in report["text"]
    assert "Strategy: 💰 Live" in report["text"]


@pytest.mark.asyncio
async def test_low_eth_is_flagged_only_while_a_monitor_is_live(chain):
    chain(balances=(0.001,))
    report = await health.scanner_health()
    assert report["low_eth"] is True
    assert any("LOW ETH BALANCE" in a for a in report["alerts"])

    OPTIONS.update(pairs_mode=1, strategy_mode=0)
    chain(balances=(0.001,))
    report = await health.scanner_health()
    assert report["low_eth"] is False


@pytest.mark.asyncio
async def test_unknown_balance_is_not_treated_as_low(chain):
    chain(balances=(None,))

    report = await health.scanner_health()

    assert report["low_eth"] is False
    assert report["eth"] is None
    assert any("ETH BALANCE UNKNOWN" in a for a in report["alerts"])


@pytest.mark.asyncio
async def test_rpc_outage_and_quiet_pairs_are_reported(chain):
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    chain(
        rpcs=((["A ❌ Timeout", "B ❌ Offline"], ["A ❌ Timeout", "B ❌ Offline"]),),
        stats=({"awaiting": 50}, None, None, {"found_at": old.replace(tzinfo=None)}),
    )

    report = await health.scanner_health()

    assert "🔥 *Net0 RPC*: all 2 nodes down" in report["alerts"]
    assert (
        "📦 *PAIRS BACKLOG*: `50` pairs waiting to be checked, 0 found in the "
        "last 24h. Checking 20 every 5 min, under 1h"
    ) in report["alerts"]
    assert any("no new pairs for 3h" in a for a in report["alerts"])


@pytest.mark.asyncio
async def test_watch_only_is_shown(chain):
    OPTIONS.update(pairs_mode=1, strategy_mode=0)
    chain()

    text = (await health.scanner_health())["text"]
    assert "Pairs: 👀 Watch" in text and "Strategy: ⏸️ Off" in text


@pytest.mark.asyncio
async def test_checking_health_never_cleans_the_database(monkeypatch, chain):
    chain()
    clear = AsyncMock()
    monkeypatch.setattr(health, "clear_archive", clear, raising=False)
    OPTIONS["db_cleanup_mb"] = 0.0001

    report = await health.scanner_health()

    clear.assert_not_awaited()
    assert "💰 *Wallet*\n └ ETH: 0.5000" in report["text"]


@pytest.mark.asyncio
async def test_watch_only_never_alerts_about_eth(chain):
    OPTIONS.update(pairs_mode=1, strategy_mode=1)
    chain(balances=(0.001,))

    report = await health.scanner_health()

    assert report["low_eth"] is False
    assert not any("ETH" in a for a in report["alerts"])
    assert "ETH: 0.0010" in report["text"]


@pytest.mark.asyncio
async def test_a_fresh_start_gets_time_to_find_pairs(chain, database, monkeypatch):
    chain(stats=({"awaiting": 0}, None, None, None))
    monkeypatch.setattr(health, "STARTED_AT", datetime.now(timezone.utc))

    assert (await health.scanner_health())["alerts"] == []

    monkeypatch.setattr(
        health, "STARTED_AT", datetime.now(timezone.utc) - timedelta(days=1)
    )
    chain(stats=({"awaiting": 0}, None, None, None))
    database()
    alerts = (await health.scanner_health())["alerts"]
    assert alerts == ["📉 *LOW PAIRS PULSE*: no new pairs found"]


@pytest.mark.asyncio
async def test_old_pairs_count_from_the_start_not_before_it(chain, monkeypatch):
    long_ago = datetime.now(timezone.utc) - timedelta(days=13)
    chain(stats=({"awaiting": 0}, None, None, {"found_at": long_ago}))
    monkeypatch.setattr(health, "STARTED_AT", datetime.now(timezone.utc))

    assert (await health.scanner_health())["alerts"] == []


@pytest.mark.asyncio
async def test_test_mode_skips_the_pairs_checks(chain, monkeypatch):
    monkeypatch.setattr(health.SETTINGS, "test_mode", True)
    chain(stats=({"awaiting": 99}, None, None, None))

    assert (await health.scanner_health())["alerts"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs,line,alert",
    [
        ({}, "🟢 1.00 MB", None),
        ({"mb": 4.2}, "🟡 4.20 MB", "still over the 4 MB clean-up level"),
        (
            {"grants": "GRANT SELECT ON `db`.* TO `u`"},
            "🔴 Read-only (1.00 MB)",
            "read-only",
        ),
        ({"error": RuntimeError("gone")}, "🔴 Offline", "connection failed"),
    ],
    ids=["fine", "still-too-big", "read-only", "offline"],
)
async def test_the_database_is_checked(chain, database, kwargs, line, alert):
    chain()
    database(**kwargs)

    report = await health.scanner_health()

    assert f"🗄️ *Database*\n └ {line}" in report["text"]
    assert report["db_problem"] is (alert is not None)
    if alert:
        assert any(alert in a for a in report["alerts"])
    else:
        assert report["alerts"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "run,alert",
    [
        ({"at": "now", "fetched": 120, "error": None}, None),
        (
            {"at": "now", "fetched": 0, "error": "fetch failed (status 429)"},
            "pool list failed (fetch failed (status 429))",
        ),
        ({"at": "now", "fetched": 0, "error": None}, "the pool list came back empty"),
        ({"at": "old", "fetched": 120, "error": None}, "hasn't run since "),
        ({}, "hasn't run since start-up, due every 60 min"),
    ],
    ids=["fine", "fetch-failed", "empty", "stale", "never-ran"],
)
async def test_the_strategy_side_is_checked_too(chain, monkeypatch, run, alert):
    now = datetime.now(timezone.utc)
    if run:
        run = {**run, "at": now if run["at"] == "now" else now - timedelta(hours=3)}
    monkeypatch.setattr(health, "STRATEGY_RUN", run)
    OPTIONS.update(strategy_mode=1, strategy_monitor_interval=60)
    chain()

    alerts = [a for a in (await health.scanner_health())["alerts"] if "STRATEGY" in a]

    assert len(alerts) == (1 if alert else 0)
    assert all(a.startswith(f"🧭 *STRATEGY*: {alert}") for a in alerts)


@pytest.mark.asyncio
async def test_a_strategy_that_is_off_is_not_checked(chain, monkeypatch):
    monkeypatch.setattr(health, "STRATEGY_RUN", {})
    OPTIONS["strategy_mode"] = 0
    chain()

    assert not any("STRATEGY" in a for a in (await health.scanner_health())["alerts"])

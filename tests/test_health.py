from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

import pair_scanner.health as health
from pair_scanner.settings import OPTIONS


@pytest.fixture
def chain(monkeypatch):
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
    assert "Trading: 🟢 ON" in report["text"]


@pytest.mark.asyncio
async def test_low_eth_is_flagged_only_while_monitors_run(chain):
    chain(balances=(0.001,))
    report = await health.scanner_health()
    assert report["low_eth"] is True
    assert any("LOW ETH BALANCE" in a for a in report["alerts"])

    OPTIONS.update(pairs_enabled=0, strategy_enabled=0)
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

    assert "🔥 *Net0 RPC*: all down" in report["alerts"]
    assert any("PAIRS BACKLOG" in a for a in report["alerts"])
    assert any("no new pairs for 3h" in a for a in report["alerts"])


@pytest.mark.asyncio
async def test_watch_only_is_shown(chain):
    OPTIONS["trading_enabled"] = 0
    chain()

    assert "Trading: 👀 Watch-only" in (await health.scanner_health())["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sizes,cleaned",
    [([4.5, 1.2], True), ([3.9], False)],
    ids=["over-the-limit", "under-the-limit"],
)
async def test_the_database_is_cleaned_near_its_limit(
    monkeypatch, chain, sizes, cleaned
):
    chain()
    monkeypatch.setattr(health, "db_total_mb", AsyncMock(side_effect=sizes))
    clear = AsyncMock()
    monkeypatch.setattr(health, "clear_archive", clear)

    report = await health.scanner_health()

    assert clear.await_count == int(cleaned)
    assert (
        "🧹 *Database auto-cleaned* — 4.50 → 1.20 MB" in report["alerts"]
    ) is cleaned


@pytest.mark.asyncio
async def test_a_failed_clean_goes_to_sentry_not_the_alerts(monkeypatch, chain):
    chain()
    monkeypatch.setattr(
        health, "db_total_mb", AsyncMock(side_effect=RuntimeError("db down"))
    )
    reported = []
    monkeypatch.setattr(health.sentry_sdk, "capture_exception", reported.append)

    report = await health.scanner_health()

    assert report["alerts"] == []
    assert str(reported[0]) == "db down"

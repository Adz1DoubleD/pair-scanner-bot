import pytest

import pair_scanner.db.pairs as pairs


@pytest.mark.asyncio
async def test_pairs_add_uses_pending_default(make_cursor, patch_session):
    cur = make_cursor()
    patch_session(pairs, cur)
    assert await pairs.pairs_add("token", "pair", "Base") is True
    assert cur.execute.call_args.args[1] == (
        "token",
        "pair",
        "Base",
        18,
        "pending",
        "pairs",
        None,
        False,
    )


@pytest.mark.asyncio
async def test_a_v4_pool_with_an_unknown_hook_is_saved_already_rejected(
    make_cursor, patch_session
):
    cur = patch_session(pairs, make_cursor())

    await pairs.pairs_add(
        "token", "0xid", "Base", source="v4", pool_key="k", status="unknown-hook"
    )

    assert cur.execute.call_args.args[1][4:] == ("unknown-hook", "v4", "k", True)


@pytest.mark.asyncio
async def test_pairs_update_metadata_sets_awaiting(make_cursor, patch_session):
    cur = make_cursor()
    patch_session(pairs, cur)
    await pairs.pairs_update_metadata("token", "ABC", 9)
    assert cur.execute.call_args.args[1] == ("ABC", 9, "token")
    assert "status = 'awaiting'" in cur.execute.call_args.args[0]


@pytest.mark.asyncio
async def test_pairs_mark_invalid(make_cursor, patch_session):
    cur = make_cursor()
    patch_session(pairs, cur)
    await pairs.pairs_mark_as_invalid("token")
    assert cur.execute.call_args.args[1] == ("token",)


@pytest.mark.asyncio
async def test_get_for_audit_combines_limited_and_always_retry_results(
    make_cursor, patch_session, monkeypatch
):
    first = [{"id": 1}]
    second = [{"id": 2}]
    cur = make_cursor(fetchall=[first, second])
    patch_session(pairs, cur)
    monkeypatch.setattr(
        pairs,
        "OPTIONS",
        {"audit_cooldown": 10, "auto_audit_max": 5, "max_audits": 2},
    )
    monkeypatch.setattr(pairs, "PAIR_RETRY_LIMITED", ["low-liquidity"])
    monkeypatch.setattr(pairs, "PAIR_RETRY_ALWAYS", ["pending"])
    assert await pairs.pairs_get_for_audit() == first + second
    assert cur.execute.call_args_list[0].args[1] == (
        "awaiting",
        "low-liquidity",
        "pairs",
        10.0,
        2,
        5,
    )
    assert cur.execute.call_args_list[1].args[1] == ("pending", "pairs", 10.0, 5)


@pytest.mark.asyncio
async def test_get_for_audit_empty_returns_list(make_cursor, patch_session):
    cur = make_cursor(fetchall=[[], []])
    patch_session(pairs, cur)
    assert await pairs.pairs_get_for_audit() == []


@pytest.mark.asyncio
async def test_rejection_counts_empty(make_cursor, patch_session):
    cur = make_cursor(fetchall=[[]])
    patch_session(pairs, cur)
    assert await pairs.pairs_get_rejection_counts() == {}


@pytest.mark.asyncio
async def test_rejection_counts_maps_rows(make_cursor, patch_session, monkeypatch):
    cur = make_cursor(fetchall=[[{"status": "high-tax", "count": 3}]])
    patch_session(pairs, cur)
    monkeypatch.setattr(pairs, "PAIR_STATUS", ["high-tax", "unverified"])
    assert await pairs.pairs_get_rejection_counts() == {"high-tax": 3}
    assert cur.execute.call_args.args[1] == ("high-tax", "unverified")


@pytest.mark.asyncio
async def test_mark_audit_complete_increments_retry_for_normal_status(
    make_cursor, patch_session, monkeypatch
):
    cur = make_cursor()
    patch_session(pairs, cur)
    monkeypatch.setattr(pairs, "PAIR_RETRY_LIMITED", ["high-tax"])
    monkeypatch.setattr(pairs, "PAIR_RETRY_ALWAYS", ["no-data"])
    await pairs.pairs_mark_audit_complete("token", "high-tax")
    assert "retry_count = retry_count + 1" in cur.execute.call_args.args[0]


@pytest.mark.asyncio
async def test_mark_audit_complete_does_not_increment_for_low_liquidity(
    make_cursor, patch_session, monkeypatch
):
    cur = make_cursor()
    patch_session(pairs, cur)
    monkeypatch.setattr(pairs, "PAIR_STATUS", ["low-liquidity"])
    await pairs.pairs_mark_audit_complete("token", "low-liquidity")
    assert "retry_count = retry_count + 1" not in cur.execute.call_args.args[0]


@pytest.mark.asyncio
async def test_reset_db_truncates_both_tables(make_cursor, patch_session):
    cur = make_cursor()
    patch_session(pairs, cur)
    await pairs.pairs_reset_db()
    assert [c.args[0] for c in cur.execute.call_args_list] == [
        "TRUNCATE TABLE pairs",
        "TRUNCATE TABLE positions",
    ]


@pytest.mark.asyncio
async def test_status_count_handles_no_row(make_cursor, patch_session):
    cur = make_cursor(fetchone=[None])
    patch_session(pairs, cur)
    assert await pairs.pairs_get_status_count("monitoring", "positions") == 0


@pytest.mark.asyncio
async def test_status_count_returns_count(make_cursor, patch_session):
    cur = make_cursor(fetchone=[{"cnt": "4"}])
    patch_session(pairs, cur)
    assert await pairs.pairs_get_status_count("monitoring", "positions") == 4


@pytest.mark.asyncio
async def test_get_by_id_returns_row(make_cursor, patch_session):
    row = {"id": 8}
    cur = make_cursor(fetchone=[row])
    patch_session(pairs, cur)
    assert await pairs.pairs_get_by_id(8, "positions") == row
    assert cur.execute.call_args.args[1] == (8,)


@pytest.mark.asyncio
async def test_get_by_status_applies_lowercase_filter_and_limit(
    make_cursor, patch_session
):
    rows = [{"id": 1}]
    cur = make_cursor(fetchall=[rows])
    patch_session(pairs, cur)
    assert await pairs.pairs_get_by_status("MONITORING", "positions", 10) == rows
    assert cur.execute.call_args.args[1] == ["monitoring", 10]


@pytest.mark.asyncio
async def test_get_by_status_without_status_or_limit(make_cursor, patch_session):
    cur = make_cursor(fetchall=[[]])
    patch_session(pairs, cur)
    assert await pairs.pairs_get_by_status(None, "pairs") == []
    assert cur.execute.call_args.args[1] == []


@pytest.mark.asyncio
async def test_position_add_converts_missing_metrics_to_zero(
    make_cursor, patch_session
):
    cur = make_cursor(lastrowid=21)
    patch_session(pairs, cur)
    result = await pairs.pairs_position_add(
        {"target_address": "token", "pair_address": "pair"}, {}, "clear", True
    )
    assert result == 21
    params = cur.execute.call_args.args[1]
    assert params[1:3] == ("pair", None)
    assert params[7:13] == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert params[15:] == (True, 0.0, 0.0, None, "monitoring")


@pytest.mark.asyncio
async def test_position_add_passes_metrics_and_tax(make_cursor, patch_session):
    cur = make_cursor(lastrowid=6)
    patch_session(pairs, cur)
    await pairs.pairs_position_add(
        {
            "target_address": "t",
            "pair_address": "p",
            "symbol": "ABC",
            "network": "ETH",
            "buy_tx": "tx",
        },
        {"price_eth": "1.2", "liq_eth": "3", "mcap_eth": "4", "decimals": 9},
        "warning",
        False,
        {"buy_tax": "2", "sell_tax": "5"},
    )
    params = cur.execute.call_args.args[1]
    assert params == (
        "t",
        "p",
        None,
        "ABC",
        "ETH",
        "tx",
        9,
        1.2,
        3.0,
        4.0,
        1.2,
        3.0,
        4.0,
        "warning",
        "warning",
        False,
        2.0,
        5.0,
        None,
        "monitoring",
    )


@pytest.mark.asyncio
async def test_position_update_preserves_taxes_when_gp_data_missing(
    make_cursor, patch_session
):
    cur = make_cursor()
    patch_session(pairs, cur)
    await pairs.pairs_position_update(
        {"price_eth": 1, "liq_eth": 2, "mcap_eth": 3}, 4, 5, "clear", True, 9
    )
    params = cur.execute.call_args.args[1]
    assert params == (1, 2, 3, 4, 4, 5, "clear", True, None, None, None, None, 9)


@pytest.mark.asyncio
async def test_mark_completed_updates_pair_and_position_with_pnl(
    make_cursor, patch_session
):
    cur = make_cursor()
    patch_session(pairs, cur)
    await pairs.pairs_mark_completed("token", 12.5)
    assert cur.execute.await_count == 2
    assert cur.execute.call_args.args[1] == (
        "completed",
        12.5,
        None,
        None,
        None,
        "token",
    )


@pytest.mark.asyncio
async def test_mark_completed_without_pnl(make_cursor, patch_session):
    cur = make_cursor()
    patch_session(pairs, cur)
    await pairs.pairs_mark_completed("token")
    assert cur.execute.await_count == 2
    assert cur.execute.call_args.args[1] == (
        "completed",
        None,
        None,
        None,
        None,
        "token",
    )


@pytest.mark.asyncio
async def test_mark_completed_saves_sell_tx_and_real_amounts(
    make_cursor, patch_session
):
    cur = make_cursor()
    patch_session(pairs, cur)
    await pairs.pairs_mark_completed(
        "token",
        pnl=-25.0,
        sell_tx="0xsell",
        eth_spent=0.0000108,
        eth_received=0.0000081,
    )
    assert cur.execute.call_args.args[1] == (
        "completed",
        -25.0,
        "0xsell",
        0.0000108,
        0.0000081,
        "token",
    )


@pytest.mark.asyncio
async def test_mark_completed_keeps_stored_values_it_is_not_given(
    make_cursor, patch_session
):
    cur = make_cursor()
    patch_session(pairs, cur)
    await pairs.pairs_mark_completed("token")
    sql = cur.execute.call_args.args[0]
    for column in ("pnl_percent", "sell_tx", "eth_spent", "eth_received"):
        assert f"{column} = COALESCE(%s, {column})" in sql


@pytest.mark.asyncio
async def test_pairs_record_strat_inserts_strat_row(make_cursor, patch_session):
    cur = make_cursor()
    patch_session(pairs, cur)

    await pairs.pairs_strat_add("0xABC", "0xDEF", "TKN", "high-tax")

    sql = cur.execute.call_args.args[0]
    params = cur.execute.call_args.args[1]

    assert "INSERT INTO pairs" in sql
    assert "'strat'" in sql
    assert "audit_done" in sql
    assert params == ("0xABC", "0xDEF", "Base", 18, "TKN", "high-tax")


@pytest.mark.asyncio
async def test_pairs_record_strat_upserts_on_conflict(make_cursor, patch_session):
    cur = make_cursor()
    patch_session(pairs, cur)

    await pairs.pairs_strat_add("0xABC", "0xDEF", "TKN", "monitoring")

    sql = cur.execute.call_args.args[0]
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "status = new.status" in sql


@pytest.mark.asyncio
async def test_pairs_get_for_audit_excludes_strat_rows(make_cursor, patch_session):
    cur = make_cursor(fetchall=[[], []])
    patch_session(pairs, cur)

    result = await pairs.pairs_get_for_audit()

    calls = cur.execute.call_args_list
    assert calls, "expected pairs_get_for_audit to run queries"
    assert all("source = %s" in c.args[0] and "pairs" in c.args[1] for c in calls)
    assert result == []


@pytest.mark.asyncio
async def test_v4_pools_are_audited_on_their_own(make_cursor, patch_session):
    cur = patch_session(pairs, make_cursor(fetchall=[[], []]))

    await pairs.pairs_get_for_audit(source="v4")

    assert all("v4" in c.args[1] for c in cur.execute.call_args_list)


@pytest.mark.asyncio
async def test_a_paper_position_closes_without_touching_the_pair(
    make_cursor, patch_session
):
    cur = make_cursor()
    patch_session(pairs, cur)

    await pairs.pairs_mark_completed("token", 20.0, status="paper-closed")

    assert cur.execute.await_count == 1
    assert cur.execute.call_args.args[1][0] == "paper-closed"


@pytest.mark.asyncio
async def test_pairs_for_audit_say_if_the_name_was_seen_in_the_last_day(
    make_cursor, patch_session
):
    cur = patch_session(pairs, make_cursor(fetchall=[[], []]))

    await pairs.pairs_get_for_audit()

    for call in cur.execute.call_args_list:
        assert "AS repeat_name" in call.args[0]
        assert "INTERVAL 1 DAY" in call.args[0]

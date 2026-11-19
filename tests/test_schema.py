import pytest

import pair_scanner.db as db
from pair_scanner.settings import OPTION_DEFAULTS, OPTIONS


def fake_db(make_cursor, tables=(), stored=(), host=None, columns=()):
    class Cursor(make_cursor):
        def __init__(self):
            super().__init__()
            self.tables = set(tables)
            self.stored = list(stored)
            self.columns = set(columns)

        async def _execute(self, *args):
            await super()._execute(*args)
            sql = " ".join(args[0].split())
            if sql.startswith("CREATE TABLE"):
                self.tables.add(sql.split()[2])
                if "half_eth" in sql:
                    self.columns.add("half_eth")
            if sql.startswith("INSERT INTO scanner_settings"):
                self.stored.append({"name": args[1][0], "value": args[1][1]})

        async def _fetchone(self):
            sql, *params = self.calls[-1]
            if sql.startswith("SELECT (SELECT COUNT(*) FROM pairs)"):
                return {"pairs": 12, "positions": 3}
            if sql.startswith("SHOW TABLES LIKE"):
                name = params[0][0] if params else sql.split("'")[1]
                return {"t": name} if name in self.tables else None
            if sql.startswith("SHOW COLUMNS FROM positions LIKE"):
                return {"f": 1} if sql.split("'")[1] in self.columns else None
            if sql.startswith("SELECT * FROM"):
                return host
            return None

        async def _fetchall(self):
            if self.calls[-1][0].startswith("SELECT name, value FROM scanner_settings"):
                return list(self.stored)
            return []

    return Cursor()


@pytest.mark.asyncio
async def test_a_new_database_gets_its_tables_and_safe_defaults(
    make_cursor, patch_session, capsys
):
    cur = patch_session(db.schema, fake_db(make_cursor))
    OPTIONS["pairs_mode"] = 2

    await db.init_scanner_db()

    assert cur.tables == {"pairs", "positions", "scanner_settings"}
    assert capsys.readouterr().out.splitlines() == [
        f"✅ Pair scanner database ready: 12 pairs, 3 positions, "
        f"{len(OPTION_DEFAULTS)} settings"
    ]
    assert OPTIONS == OPTION_DEFAULTS
    assert (OPTIONS["pairs_mode"], OPTIONS["strategy_mode"]) == (1, 0)


@pytest.mark.asyncio
async def test_saved_settings_are_loaded_with_their_types(make_cursor, patch_session):
    patch_session(
        db.schema,
        fake_db(
            make_cursor,
            tables={"pairs", "positions", "scanner_settings"},
            stored=[
                {"name": "max_positions", "value": "7"},
                {"name": "buy_amount_eth", "value": "0.005"},
                {"name": "retired_setting", "value": "1"},
            ],
        ),
    )

    await db.init_scanner_db()

    assert OPTIONS["max_positions"] == 7
    assert OPTIONS["buy_amount_eth"] == 0.005
    assert "retired_setting" not in OPTIONS


@pytest.mark.asyncio
async def test_option_set_saves_and_updates(make_cursor, patch_session):
    cur = patch_session(db.options, make_cursor())

    assert await db.option_set("max_positions", "4") == 4

    sql, params = cur.calls[-1]
    assert sql.startswith("INSERT INTO scanner_settings")
    assert params == ("max_positions", "4")
    assert OPTIONS["max_positions"] == 4


@pytest.mark.asyncio
async def test_option_set_refuses_unknown_settings(make_cursor, patch_session):
    patch_session(db.options, make_cursor())

    with pytest.raises(ValueError):
        await db.option_set("private_key", "0x1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored,pairs,strategy",
    [
        ({"trading_enabled": "1"}, 2, 0),
        ({"trading_enabled": "0"}, 1, 0),
        ({"trading_enabled": "1", "strategy_enabled": "1"}, 2, 2),
        ({"trading_enabled": "1", "pairs_enabled": "0"}, 0, 0),
        ({"pairs_enabled": "1", "strategy_enabled": "1"}, 1, 1),
        ({"mode": "2", "pairs_enabled": "1", "strategy_enabled": "1"}, 2, 2),
        ({"mode": "0", "pairs_enabled": "1"}, 0, 0),
    ],
)
async def test_the_old_switches_become_a_mode_per_monitor(
    make_cursor, patch_session, stored, pairs, strategy
):
    cur = patch_session(
        db.schema,
        fake_db(
            make_cursor,
            tables={"pairs", "positions", "scanner_settings"},
            stored=[{"name": k, "value": v} for k, v in stored.items()],
        ),
    )

    await db.init_scanner_db()

    assert (OPTIONS["pairs_mode"], OPTIONS["strategy_mode"]) == (pairs, strategy)
    sql = [c for c in cur.calls if "scanner_settings" in c[0]]
    assert (
        "INSERT INTO scanner_settings (name, value) VALUES (%s, %s)",
        ("pairs_mode", str(pairs)),
    ) in sql
    assert any(
        c[0].startswith("DELETE FROM scanner_settings WHERE name IN") for c in sql
    )


@pytest.mark.asyncio
async def test_saved_monitor_modes_are_not_migrated_again(make_cursor, patch_session):
    cur = patch_session(
        db.schema,
        fake_db(
            make_cursor,
            tables={"pairs", "positions", "scanner_settings"},
            stored=[{"name": "pairs_mode", "value": "0"}],
        ),
    )

    await db.init_scanner_db()

    assert OPTIONS["pairs_mode"] == 0
    assert not any(c[0].startswith(("INSERT", "DELETE")) for c in cur.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tables,altered", [((), False), (("pairs", "positions"), True)]
)
async def test_older_positions_tables_get_the_half_sale_column(
    make_cursor, patch_session, tables, altered
):
    cur = patch_session(db.schema, fake_db(make_cursor, tables=tables))

    await db.init_scanner_db()

    alters = [c[0] for c in cur.calls if c[0].startswith("ALTER TABLE positions")]
    assert bool(alters) is altered

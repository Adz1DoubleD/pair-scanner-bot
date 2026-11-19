import pytest

import pair_scanner.db as db
from pair_scanner.settings import OPTION_DEFAULTS, OPTIONS


def fake_db(make_cursor, tables=(), stored=(), host=None):
    class Cursor(make_cursor):
        def __init__(self):
            super().__init__()
            self.tables = set(tables)
            self.stored = list(stored)

        async def _execute(self, *args):
            await super()._execute(*args)
            sql = " ".join(args[0].split())
            if sql.startswith("CREATE TABLE"):
                self.tables.add(sql.split()[2])
            if sql.startswith("INSERT INTO scanner_settings"):
                self.stored.append({"name": args[1][0], "value": args[1][1]})

        async def _fetchone(self):
            sql, *params = self.calls[-1]
            if sql.startswith("SELECT (SELECT COUNT(*) FROM pairs)"):
                return {"pairs": 12, "positions": 3}
            if sql.startswith("SHOW TABLES LIKE"):
                name = params[0][0] if params else sql.split("'")[1]
                return {"t": name} if name in self.tables else None
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
    OPTIONS["mode"] = 2

    await db.init_scanner_db()

    assert cur.tables == {"pairs", "positions", "scanner_settings"}
    assert capsys.readouterr().out.splitlines() == [
        f"✅ Pair scanner database ready: 12 pairs, 3 positions, "
        f"{len(OPTION_DEFAULTS)} settings"
    ]
    assert OPTIONS == OPTION_DEFAULTS
    assert OPTIONS["mode"] == 1


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
@pytest.mark.parametrize("old,mode", [("1", 2), ("0", 1)])
async def test_the_old_trading_switch_becomes_the_mode(
    make_cursor, patch_session, old, mode
):
    cur = patch_session(
        db.schema,
        fake_db(
            make_cursor,
            tables={"pairs", "positions", "scanner_settings"},
            stored=[{"name": "trading_enabled", "value": old}],
        ),
    )

    await db.init_scanner_db()

    assert OPTIONS["mode"] == mode
    sql = [c[0] for c in cur.calls]
    assert "DELETE FROM scanner_settings WHERE name = 'trading_enabled'" in sql

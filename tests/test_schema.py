from decimal import Decimal

import pytest

import pair_scanner.db as db
from pair_scanner.settings import OPTION_DEFAULTS, OPTIONS, SETTINGS


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
    make_cursor, patch_session
):
    cur = patch_session(db.schema, fake_db(make_cursor))
    OPTIONS["trading_enabled"] = 1

    imported = await db.init_scanner_db()

    assert imported == 0
    assert cur.tables == {"pairs", "positions", "scanner_settings"}
    assert OPTIONS == OPTION_DEFAULTS
    assert OPTIONS["trading_enabled"] == 0


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


HOST_ROW = {
    "id": 1,
    "ram_warning_mb": 400,
    "tesla_refresh_token": "not-ours",
    "pairs_enabled": 1,
    "strategy_enabled": 1,
    "buy_amount_eth": Decimal("0.00001000"),
    "max_positions": 5,
    "take_profit_percent": 200,
    "eth_bal_snapshot": None,
}


@pytest.mark.asyncio
async def test_the_hosts_scanner_settings_are_copied_once(
    monkeypatch, make_cursor, patch_session
):
    monkeypatch.setattr(SETTINGS, "import_settings_from", "settings")
    cur = patch_session(
        db.schema,
        fake_db(make_cursor, tables={"settings"}, host=HOST_ROW),
    )

    imported = await db.init_scanner_db()

    copied = {r["name"]: r["value"] for r in cur.stored}
    assert copied == {
        "trading_enabled": "1",
        "min_eth_balance": "0.001",
        "pairs_enabled": "1",
        "strategy_enabled": "1",
        "buy_amount_eth": "1e-05",
        "max_positions": "5",
        "take_profit_percent": "200",
    }
    assert imported == 7
    assert OPTIONS["trading_enabled"] == 1
    assert OPTIONS["take_profit_percent"] == 200
    assert OPTIONS["buy_amount_eth"] == 0.00001


@pytest.mark.asyncio
async def test_settings_are_not_copied_again_once_saved(
    monkeypatch, make_cursor, patch_session
):
    monkeypatch.setattr(SETTINGS, "import_settings_from", "settings")
    cur = patch_session(
        db.schema,
        fake_db(
            make_cursor,
            tables={"settings", "scanner_settings"},
            stored=[{"name": "max_positions", "value": "2"}],
            host=HOST_ROW,
        ),
    )

    assert await db.init_scanner_db() == 0
    assert not any(c[0].startswith("SELECT * FROM") for c in cur.calls)
    assert OPTIONS["max_positions"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [None, "settings; DROP TABLE pairs", "missing"])
async def test_nothing_is_copied_without_a_valid_source(
    monkeypatch, make_cursor, patch_session, source
):
    monkeypatch.setattr(SETTINGS, "import_settings_from", source)
    cur = patch_session(db.schema, fake_db(make_cursor, host=HOST_ROW))

    assert await db.init_scanner_db() == 0
    assert not any(c[0].startswith("SELECT * FROM") for c in cur.calls)
    assert OPTIONS["trading_enabled"] == 0


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

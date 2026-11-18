from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

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

    assert imported == {"settings": 0, "pairs": 0, "positions": 0}
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
    assert imported == {"settings": 7, "pairs": 0, "positions": 0}
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

    assert await db.init_scanner_db() == {"settings": 0, "pairs": 0, "positions": 0}
    assert not any(c[0].startswith("SELECT * FROM") for c in cur.calls)
    assert OPTIONS["max_positions"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [None, "settings; DROP TABLE pairs", "missing"])
async def test_nothing_is_copied_without_a_valid_source(
    monkeypatch, make_cursor, patch_session, source
):
    monkeypatch.setattr(SETTINGS, "import_settings_from", source)
    cur = patch_session(db.schema, fake_db(make_cursor, host=HOST_ROW))

    assert await db.init_scanner_db() == {"settings": 0, "pairs": 0, "positions": 0}
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


class HostCursor:
    def __init__(self, tables):
        self.tables = tables
        self.last = None
        self.writes = []

    async def execute(self, sql, params=None):
        self.last = (" ".join(sql.split()), params)
        if not sql.startswith(("SHOW", "SELECT")):
            self.writes.append(sql)

    async def fetchone(self):
        sql, params = self.last
        if sql.startswith("SHOW TABLES LIKE"):
            return {"t": 1} if params[0] in self.tables else None
        return self.tables.get("settings", [None])[0]

    async def fetchall(self):
        return self.tables.get(self.last[0].split("`")[1], [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_settings_and_history_are_copied_from_the_hosts_database(
    monkeypatch, make_cursor, patch_session
):
    host = HostCursor(
        {
            "settings": [{"id": 1, "max_positions": 5, "tesla_refresh_token": "x"}],
            "pairs": [
                {"id": 3, "target_address": "0xa", "status": "awaiting"},
                {"id": 9, "target_address": "0xb", "status": "low-ratio"},
            ],
            "positions": [{"id": 4, "target_address": "0xa", "status": "monitoring"}],
        }
    )
    closed = []
    connection = SimpleNamespace(
        cursor=lambda *a: host, close=lambda: closed.append(True)
    )
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(db.schema.aiomysql, "connect", connect)
    monkeypatch.setattr(SETTINGS, "db_url", "mysql://u:p@scanner:3306/scan")
    monkeypatch.setattr(SETTINGS, "import_from_db_url", "mysql://u:p@host:3306/yarco")
    monkeypatch.setattr(SETTINGS, "import_settings_from", "settings")

    class Cursor(make_cursor):
        async def _fetchone(self):
            sql = self.calls[-1][0]
            if sql.startswith("SELECT COUNT(*)"):
                return {"n": 0}
            return None

        async def _fetchall(self):
            sql = self.calls[-1][0]
            if sql.startswith("SHOW COLUMNS FROM `pairs`"):
                return [{"Field": f} for f in ("id", "target_address", "status")]
            if sql.startswith("SHOW COLUMNS FROM `positions`"):
                return [{"Field": f} for f in ("id", "target_address", "status")]
            return []

    cur = patch_session(db.schema, Cursor())
    cur.executemany = AsyncMock()

    copied = await db.init_scanner_db()

    assert copied == {"settings": 3, "pairs": 2, "positions": 1}
    assert connect.await_args.kwargs["host"] == "host"
    assert connect.await_args.kwargs["db"] == "yarco"
    assert host.writes == [] and closed == [True]
    inserts = {
        c.args[0].split("`")[1]: c.args[1] for c in cur.executemany.await_args_list
    }
    assert inserts["pairs"] == [[3, "0xa", "awaiting"], [9, "0xb", "low-ratio"]]
    assert inserts["positions"] == [[4, "0xa", "monitoring"]]
    sql = [c[0] for c in cur.calls]
    assert sql.index("START TRANSACTION") < sql.index("COMMIT")
    assert OPTIONS["max_positions"] == 5 and OPTIONS["trading_enabled"] == 1

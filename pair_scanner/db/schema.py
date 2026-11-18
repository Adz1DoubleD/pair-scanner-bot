import re
from urllib.parse import urlparse

import aiomysql

from ..settings import OPTION_DEFAULTS, OPTIONS, SETTINGS, option_value
from .core import db_session

IMPORTED_EXTRAS = {"trading_enabled": 1, "min_eth_balance": 0.001}


async def init_scanner_db():
    async with db_session() as cur:
        await cur.execute("SHOW TABLES LIKE 'pairs'")
        if not await cur.fetchone():
            await cur.execute("""
                CREATE TABLE pairs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    target_address VARCHAR(42) UNIQUE,
                    pair_address VARCHAR(42),
                    symbol VARCHAR(20),
                    decimals INT,
                    network VARCHAR(50),
                    status VARCHAR(50) DEFAULT 'pending',
                    source VARCHAR(10) NOT NULL DEFAULT 'pairs',
                    found_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    audit_done BOOLEAN DEFAULT FALSE,
                    retry_count INT DEFAULT 0,
                    INDEX (status),
                    INDEX (source)
                )
            """)

        await cur.execute("SHOW TABLES LIKE 'positions'")
        if not await cur.fetchone():
            await cur.execute("""
                CREATE TABLE positions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    target_address VARCHAR(42) UNIQUE NOT NULL,
                    pair_address VARCHAR(42) NOT NULL,
                    symbol VARCHAR(20),
                    network VARCHAR(50) DEFAULT 'Base',
                    buy_tx VARCHAR(66) DEFAULT NULL,
                    sell_tx VARCHAR(66) DEFAULT NULL,
                    eth_spent DECIMAL(36, 18) DEFAULT NULL,
                    eth_received DECIMAL(36, 18) DEFAULT NULL,
                    decimals INT DEFAULT 18,
                    entry_price_eth DECIMAL(36, 18),
                    entry_liq_eth DECIMAL(36, 18),
                    entry_mcap_eth DECIMAL(36, 18),
                    current_price_eth DECIMAL(36, 18),
                    current_liq_eth DECIMAL(36, 18),
                    current_mcap_eth DECIMAL(36, 18),
                    pnl_percent DECIMAL(12, 4) DEFAULT 0.0000,
                    peak_pnl_percent DECIMAL(12, 4) DEFAULT 0.0000,
                    liq_drift_percent DECIMAL(12, 4) DEFAULT 0.0000,
                    entry_sec_status VARCHAR(50) DEFAULT 'clear',
                    current_sec_status VARCHAR(50) DEFAULT 'clear',
                    status VARCHAR(20) DEFAULT 'monitoring',
                    is_renounced BOOLEAN DEFAULT FALSE,
                    buy_tax DECIMAL(5, 2) DEFAULT NULL,
                    sell_tax DECIMAL(5, 2) DEFAULT NULL,
                    tracked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP,
                    INDEX (status),
                    INDEX (pnl_percent)
                )
            """)

        await cur.execute("SHOW TABLES LIKE 'scanner_settings'")
        if not await cur.fetchone():
            await cur.execute("""
                CREATE TABLE scanner_settings (
                    name VARCHAR(64) PRIMARY KEY,
                    value VARCHAR(64) NOT NULL
                )
            """)

        await cur.execute("SELECT name, value FROM scanner_settings")
        rows = list(await cur.fetchall() or [])

        copied = {"settings": 0, "pairs": 0, "positions": 0}
        source = SETTINGS.import_settings_from
        host_url = SETTINGS.import_from_db_url
        if not rows and source and re.fullmatch(r"\w+", source):
            settings_row = None
            history = {}
            if host_url and host_url != SETTINGS.db_url:
                url = urlparse(host_url)
                conn = await aiomysql.connect(
                    host=url.hostname,
                    user=url.username,
                    password=url.password,
                    port=url.port or 3306,
                    db=url.path[1:],
                    autocommit=True,
                )
                try:
                    async with conn.cursor(aiomysql.DictCursor) as host:
                        await host.execute("SHOW TABLES LIKE %s", (source,))
                        if await host.fetchone():
                            await host.execute(f"SELECT * FROM `{source}` LIMIT 1")
                            settings_row = await host.fetchone()
                        for table in ("pairs", "positions"):
                            await host.execute("SHOW TABLES LIKE %s", (table,))
                            if await host.fetchone():
                                await host.execute(
                                    f"SELECT * FROM `{table}` ORDER BY id"
                                )
                                history[table] = list(await host.fetchall() or [])
                finally:
                    conn.close()
            else:
                await cur.execute("SHOW TABLES LIKE %s", (source,))
                if await cur.fetchone():
                    await cur.execute(f"SELECT * FROM `{source}` LIMIT 1")
                    settings_row = await cur.fetchone()

            if settings_row is not None:
                values = {
                    **IMPORTED_EXTRAS,
                    **{
                        k: v
                        for k, v in settings_row.items()
                        if k in OPTION_DEFAULTS and v is not None
                    },
                }
                await cur.execute("START TRANSACTION")
                try:
                    for table, table_rows in history.items():
                        await cur.execute(f"SELECT COUNT(*) AS n FROM `{table}`")
                        if not table_rows or (await cur.fetchone())["n"]:
                            continue
                        await cur.execute(f"SHOW COLUMNS FROM `{table}`")
                        columns = [c["Field"] for c in await cur.fetchall()]
                        keep = [c for c in columns if c in table_rows[0]]
                        await cur.executemany(
                            f"INSERT INTO `{table}` ("
                            + ", ".join(f"`{c}`" for c in keep)
                            + ") VALUES ("
                            + ", ".join(["%s"] * len(keep))
                            + ")",
                            [[r.get(c) for c in keep] for r in table_rows],
                        )
                        copied[table] = len(table_rows)
                    for name, value in values.items():
                        await cur.execute(
                            "INSERT INTO scanner_settings (name, value) "
                            "VALUES (%s, %s)",
                            (name, str(option_value(name, value))),
                        )
                    await cur.execute("COMMIT")
                except Exception:
                    await cur.execute("ROLLBACK")
                    raise
                rows = [{"name": k, "value": v} for k, v in values.items()]
                copied["settings"] = len(values)

    OPTIONS.clear()
    OPTIONS.update(OPTION_DEFAULTS)
    for row in rows:
        if row["name"] in OPTION_DEFAULTS:
            OPTIONS[row["name"]] = option_value(row["name"], row["value"])
    return copied

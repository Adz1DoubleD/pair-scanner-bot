"""Creating the tables and loading the saved settings on start."""

from ..settings import (
    OPTION_DEFAULTS,
    OPTIONS,
    option_value,
)
from .core import db_session


async def init_scanner_db() -> None:
    async with db_session() as cur:
        await cur.execute("SHOW TABLES LIKE 'pairs'")
        if not await cur.fetchone():
            await cur.execute("""
                CREATE TABLE pairs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    target_address VARCHAR(42) UNIQUE,
                    pair_address VARCHAR(66),
                    pool_key VARCHAR(160) DEFAULT NULL,
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

        await cur.execute("SHOW COLUMNS FROM pairs LIKE 'pool_key'")
        if not await cur.fetchone():
            await cur.execute(
                "ALTER TABLE pairs MODIFY pair_address VARCHAR(66), "
                "ADD COLUMN pool_key VARCHAR(160) DEFAULT NULL AFTER pair_address"
            )

        await cur.execute("SHOW TABLES LIKE 'positions'")
        if not await cur.fetchone():
            await cur.execute("""
                CREATE TABLE positions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    target_address VARCHAR(42) UNIQUE NOT NULL,
                    pair_address VARCHAR(66) NOT NULL,
                    pool_key VARCHAR(160) DEFAULT NULL,
                    symbol VARCHAR(20),
                    network VARCHAR(50) DEFAULT 'Base',
                    buy_tx VARCHAR(66) DEFAULT NULL,
                    sell_tx VARCHAR(66) DEFAULT NULL,
                    eth_spent DECIMAL(36, 18) DEFAULT NULL,
                    eth_received DECIMAL(36, 18) DEFAULT NULL,
                    half_eth DECIMAL(36, 18) DEFAULT NULL,
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

        await cur.execute("SHOW COLUMNS FROM positions LIKE 'pool_key'")
        if not await cur.fetchone():
            await cur.execute(
                "ALTER TABLE positions MODIFY pair_address VARCHAR(66) NOT NULL, "
                "ADD COLUMN pool_key VARCHAR(160) DEFAULT NULL AFTER pair_address"
            )

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

    OPTIONS.clear()
    OPTIONS.update(OPTION_DEFAULTS)
    for row in rows:
        if row["name"] in OPTION_DEFAULTS:
            OPTIONS[row["name"]] = option_value(row["name"], row["value"])

    async with db_session() as cur:
        await cur.execute(
            "SELECT (SELECT COUNT(*) FROM pairs) AS pairs, "
            "(SELECT COUNT(*) FROM positions) AS positions"
        )
        counts = await cur.fetchone()
    print(
        f"✅ Pair scanner database ready: {counts['pairs']} pairs, "
        f"{counts['positions']} positions, {len(OPTIONS)} settings"
    )

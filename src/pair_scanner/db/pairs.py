"""Reading and writing pairs and positions, and the dashboard stats."""

from typing import Any

from ..constants import PAIR_RETRY_ALWAYS, PAIR_RETRY_LIMITED, PAIR_STATUS
from ..settings import OPTIONS
from .core import db_session


async def pairs_add(
    target_address: str,
    pair_address: str,
    network: str,
    decimals: int = 18,
    source: str = "pairs",
    pool_key: str | None = None,
    status: str = "pending",
) -> bool:
    async with db_session() as cur:
        await cur.execute(
            """
            INSERT IGNORE INTO pairs
            (target_address, pair_address, network, decimals, status, source,
             pool_key, audit_done)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
            (
                target_address,
                pair_address,
                network,
                decimals,
                status,
                source,
                pool_key,
                status != "pending",
            ),
        )
        return True


async def pairs_update_metadata(address: str, symbol: str, decimals: int) -> None:
    async with db_session() as cur:
        await cur.execute(
            """
            UPDATE pairs
            SET symbol = %s, decimals = %s, status = 'awaiting'
            WHERE target_address = %s
        """,
            (symbol, decimals, address),
        )


async def pairs_mark_as_invalid(address: str) -> None:
    async with db_session() as cur:
        await cur.execute(
            "UPDATE pairs SET status = 'invalid' WHERE target_address = %s", (address,)
        )


async def pairs_get_for_audit(source: str = "pairs") -> list[dict[str, Any]]:
    cooldown = float(OPTIONS.get("audit_cooldown", 120))
    audit_limit = int(OPTIONS.get("auto_audit_max", 20))
    max_retries = int(OPTIONS.get("max_audits", 3))

    async with db_session() as cur:
        statuses = ["awaiting"] + PAIR_RETRY_LIMITED
        placeholders = ",".join(["%s"] * len(statuses))

        await cur.execute(
            f"""
            SELECT id, target_address, pair_address, network, symbol, decimals,
                pool_key
            FROM pairs
            WHERE status IN ({placeholders})
            AND source = %s
            AND found_at <= (NOW() - INTERVAL %s MINUTE)
            AND retry_count < %s
            ORDER BY found_at ASC
            LIMIT %s
        """,
            (*statuses, source, cooldown, max_retries, audit_limit),
        )
        res = await cur.fetchall()

        always_placeholders = ",".join(["%s"] * len(PAIR_RETRY_ALWAYS))
        await cur.execute(
            f"""
            SELECT id, target_address, pair_address, network, symbol, decimals,
                pool_key
            FROM pairs
            WHERE status IN ({always_placeholders})
            AND source = %s
            AND found_at <= (NOW() - INTERVAL %s MINUTE)
            ORDER BY found_at ASC
            LIMIT %s
        """,
            (*PAIR_RETRY_ALWAYS, source, cooldown, audit_limit),
        )
        always_res = await cur.fetchall()

        combined = list(res or []) + list(always_res or [])
        return combined if combined else []


async def pairs_get_rejection_counts() -> dict[str, int]:
    async with db_session() as cur:
        placeholders = ",".join(["%s"] * len(PAIR_STATUS))
        await cur.execute(
            f"SELECT status, COUNT(*) as count FROM pairs WHERE status IN ({placeholders}) GROUP BY status",
            tuple(PAIR_STATUS),
        )
        rows = await cur.fetchall()
        return {row["status"]: row["count"] for row in rows} if rows else {}


async def pairs_mark_audit_complete(address: str, final_status: str) -> None:
    async with db_session() as cur:
        if final_status in PAIR_RETRY_LIMITED or final_status in PAIR_RETRY_ALWAYS:
            await cur.execute(
                """
                UPDATE pairs
                SET audit_done = TRUE,
                    retry_count = retry_count + 1,
                    status = %s
                WHERE target_address = %s
                """,
                (final_status, address),
            )
        else:
            await cur.execute(
                """
                UPDATE pairs
                SET audit_done = TRUE, status = %s
                WHERE target_address = %s
                """,
                (final_status, address),
            )


async def clear_archive() -> int:
    async with db_session() as cur:
        placeholders = ",".join(["%s"] * len(PAIR_STATUS))
        await cur.execute(
            f"DELETE FROM pairs WHERE status IN ({placeholders})",
            tuple(PAIR_STATUS),
        )
        removed = cur.rowcount
        await cur.execute(
            """
            DELETE FROM positions
            WHERE status IN ('completed', 'paper-closed')
              AND last_updated < NOW() - INTERVAL 7 DAY
            """
        )
        removed += cur.rowcount
        await cur.execute("OPTIMIZE TABLE pairs, positions")
    return removed


async def db_total_mb() -> float:
    async with db_session() as cur:
        await cur.execute("SET SESSION information_schema_stats_expiry = 0")
        await cur.execute(
            """
            SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS mb
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            """
        )
        row = await cur.fetchone()
        return float((row or {}).get("mb") or 0)


async def pairs_reset_db() -> None:
    async with db_session() as cur:
        await cur.execute("TRUNCATE TABLE pairs")
        await cur.execute("TRUNCATE TABLE positions")


async def pairs_get_status_count(status: str, table: str) -> int:
    async with db_session() as cur:
        await cur.execute(
            f"SELECT COUNT(*) as cnt FROM {table} WHERE status = %s", (status,)
        )
        res = await cur.fetchone()
        return int(res["cnt"] or 0) if res else 0


async def pairs_get_by_id(row_id: int | str, table_name: str) -> dict[str, Any] | None:
    async with db_session() as cur:
        await cur.execute(f"SELECT * FROM {table_name} WHERE id = %s", (row_id,))
        return await cur.fetchone()


async def pairs_get_by_status(
    status: str | None, table: str, limit: int | None = None
) -> list[dict[str, Any]]:
    async with db_session() as cur:
        query = f"SELECT * FROM {table}"
        params: list[Any] = []
        if status:
            query += " WHERE status = %s"
            params.append(status.lower())
        query += " ORDER BY id DESC"
        if limit:
            query += " LIMIT %s"
            params.append(limit)
        await cur.execute(query, params)
        return await cur.fetchall()


async def pairs_get_stats(
    days: int = 1,
) -> tuple[
    dict[str, Any],
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    stats_dict: dict[str, Any] = {
        "total": 0,
        "pending": 0,
        "awaiting": 0,
        "monitoring": 0,
        "completed": 0,
        **{status: 0 for status in PAIR_STATUS},
        "active_positions": 0,
        "monitors": {
            monitor: {
                "found": 0,
                "rejected": 0,
                "watched": 0,
                "bought": 0,
                "closed": 0,
                "avg_pnl": 0.0,
                "wins": 0,
                "eth_pnl": 0.0,
                "eth_counted": 0,
                "would_closed": 0,
                "would_avg": 0.0,
                "would_wins": 0,
            }
            for monitor in ("pairs", "strategy")
        },
    }

    window = "{col} >= NOW() - INTERVAL %s DAY" if days else "1=1"
    window_args = (days,) if days else ()

    async with db_session() as cur:
        await cur.execute("SELECT status, COUNT(*) as count FROM pairs GROUP BY status")
        rows = await cur.fetchall()
        for row in rows:
            status = str(row["status"] or "").lower()
            if status in stats_dict:
                stats_dict[status] = row["count"]
            stats_dict["total"] += row["count"]

        rejection_placeholders = ",".join(["%s"] * len(PAIR_STATUS))
        await cur.execute(
            f"""
            SELECT
                IF(source = 'strat', 'strategy', 'pairs') AS monitor,
                COUNT(*) AS found,
                SUM(status IN ({rejection_placeholders})) AS rejected,
                SUM(status = 'watched') AS watched
            FROM pairs
            WHERE {window.format(col="found_at")}
            GROUP BY monitor
            """,
            (*PAIR_STATUS, *window_args),
        )
        for row in await cur.fetchall() or []:
            stats_dict["monitors"][row["monitor"]].update(
                found=int(row["found"] or 0),
                rejected=int(row["rejected"] or 0),
                watched=int(row["watched"] or 0),
            )

        sold = f"x.status = 'completed' AND {window.format(col='x.last_updated')}"
        paper = f"x.status = 'paper-closed' AND {window.format(col='x.last_updated')}"
        await cur.execute(
            f"""
            SELECT
                IF(p.source = 'strat', 'strategy', 'pairs') AS monitor,
                SUM(
                    x.status IN ('monitoring', 'completed')
                    AND {window.format(col="x.tracked_at")}
                ) AS bought,
                SUM({sold}) AS closed,
                AVG(CASE WHEN {sold} THEN x.pnl_percent END) AS avg_pnl,
                SUM({sold} AND x.pnl_percent > 0) AS wins,
                SUM(CASE WHEN {sold} THEN x.eth_received - x.eth_spent END) AS eth_pnl,
                COUNT(CASE WHEN {sold} THEN x.eth_received - x.eth_spent END)
                    AS eth_counted,
                SUM({paper}) AS would_closed,
                AVG(CASE WHEN {paper} THEN x.pnl_percent END) AS would_avg,
                SUM({paper} AND x.pnl_percent > 0) AS would_wins
            FROM positions x
            LEFT JOIN pairs p ON p.target_address = x.target_address
            GROUP BY monitor
            """,
            window_args * 9,
        )
        for row in await cur.fetchall() or []:
            stats_dict["monitors"][row["monitor"]].update(
                bought=int(row["bought"] or 0),
                closed=int(row["closed"] or 0),
                avg_pnl=float(row["avg_pnl"] or 0),
                wins=int(row["wins"] or 0),
                eth_pnl=float(row["eth_pnl"] or 0),
                eth_counted=int(row["eth_counted"] or 0),
                would_closed=int(row["would_closed"] or 0),
                would_avg=float(row["would_avg"] or 0),
                would_wins=int(row["would_wins"] or 0),
            )

        await cur.execute(
            "SELECT COUNT(*) as active FROM positions WHERE status = 'monitoring'"
        )
        pos_res = await cur.fetchone()
        stats_dict["active_positions"] = int(pos_res["active"] or 0) if pos_res else 0

        await cur.execute("""
            SELECT id, target_address, symbol, pnl_percent, tracked_at as found_at, status
            FROM positions
            WHERE status IN ('monitoring', 'completed')
            ORDER BY tracked_at DESC LIMIT 1
        """)
        last_buy = await cur.fetchone()

        await cur.execute("""
            SELECT id, target_address, symbol, pnl_percent, last_updated as found_at, status
            FROM positions
            WHERE status = 'completed'
            ORDER BY last_updated DESC LIMIT 1
        """)
        last_sell = await cur.fetchone()

        await cur.execute("""
            SELECT id, target_address, symbol, found_at, status
            FROM pairs ORDER BY found_at DESC LIMIT 1
        """)
        last_scan = await cur.fetchone()

        return stats_dict, last_buy, last_sell, last_scan


async def pairs_position_add(
    entry: dict[str, Any],
    metrics: dict[str, Any],
    sec_status: str | None,
    is_renounced: bool | None,
    gp_data: dict[str, Any] | None = None,
) -> int:
    p = float(metrics.get("price_eth") or 0)
    liq = float(metrics.get("liq_eth") or 0)
    m = float(metrics.get("mcap_eth") or 0)
    buy_tax = float((gp_data or {}).get("buy_tax") or 0)
    sell_tax = float((gp_data or {}).get("sell_tax") or 0)
    buy_tx = entry.get("buy_tx")
    decimals = metrics.get("decimals", 18)

    async with db_session() as cur:
        query = """
            INSERT INTO positions
            (target_address, pair_address, symbol, network, buy_tx, decimals,
             entry_price_eth, entry_liq_eth, entry_mcap_eth,
             current_price_eth, current_liq_eth, current_mcap_eth,
             entry_sec_status, current_sec_status, is_renounced,
             buy_tax, sell_tax, eth_spent, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s)
            AS new
            ON DUPLICATE KEY UPDATE
                current_price_eth = new.current_price_eth,
                current_liq_eth = new.current_liq_eth,
                current_mcap_eth = new.current_mcap_eth,
                current_sec_status = new.current_sec_status,
                is_renounced = new.is_renounced,
                buy_tax = new.buy_tax,
                sell_tax = new.sell_tax,
                last_updated = NOW()
        """

        await cur.execute(
            query,
            (
                entry["target_address"],
                entry["pair_address"],
                entry.get("symbol"),
                entry.get("network", "Base"),
                buy_tx,
                decimals,
                p,
                liq,
                m,
                p,
                liq,
                m,
                sec_status,
                sec_status,
                is_renounced,
                buy_tax,
                sell_tax,
                entry.get("eth_spent"),
                entry.get("status", "monitoring"),
            ),
        )
        return cur.lastrowid


async def pairs_position_update(
    metrics: dict[str, Any],
    pnl: float,
    liq_drift: float,
    sec_status: str | None,
    is_renounced: bool | None,
    pos_id: int,
    gp_data: dict[str, Any] | None = None,
    half_eth: float | None = None,
    eth_spent: float | None = None,
) -> None:
    async with db_session() as cur:
        query = """
            UPDATE positions
            SET
                current_price_eth = %s,
                current_liq_eth = %s,
                current_mcap_eth = %s,
                pnl_percent = %s,
                peak_pnl_percent = GREATEST(peak_pnl_percent, %s),
                liq_drift_percent = %s,
                current_sec_status = %s,
                is_renounced = %s,
                buy_tax = COALESCE(%s, buy_tax),
                sell_tax = COALESCE(%s, sell_tax),
                half_eth = COALESCE(%s, half_eth),
                eth_spent = COALESCE(%s, eth_spent),
                last_updated = NOW()
            WHERE id = %s
        """
        buy_tax = gp_data.get("buy_tax") if gp_data else None
        sell_tax = gp_data.get("sell_tax") if gp_data else None

        await cur.execute(
            query,
            (
                metrics["price_eth"],
                metrics["liq_eth"],
                metrics["mcap_eth"],
                pnl,
                pnl,
                liq_drift,
                sec_status,
                is_renounced,
                buy_tax,
                sell_tax,
                half_eth,
                eth_spent,
                pos_id,
            ),
        )


async def pairs_mark_completed(
    address: str,
    pnl: float | None = None,
    sell_tx: str | None = None,
    eth_spent: float | None = None,
    eth_received: float | None = None,
    status: str = "completed",
) -> None:
    async with db_session() as cur:
        if status == "completed":
            await cur.execute(
                "UPDATE pairs SET status = 'completed' WHERE target_address = %s",
                (address,),
            )
        await cur.execute(
            "UPDATE positions SET status = %s, "
            "pnl_percent = COALESCE(%s, pnl_percent), "
            "sell_tx = COALESCE(%s, sell_tx), "
            "eth_spent = COALESCE(%s, eth_spent), "
            "eth_received = COALESCE(%s, eth_received) "
            "WHERE target_address = %s",
            (status, pnl, sell_tx, eth_spent, eth_received, address),
        )


async def pairs_mark_for_retry(address: str) -> None:
    async with db_session() as cur:
        await cur.execute(
            """
            UPDATE pairs
            SET retry_count = retry_count + 1, status = 'awaiting'
            WHERE target_address = %s
            """,
            (address,),
        )


async def pairs_strat_add(
    target_address: str,
    pair_address: str,
    symbol: str,
    status: str,
    network: str = "Base",
) -> None:
    async with db_session() as cur:
        await cur.execute(
            """
            INSERT INTO pairs
                (target_address, pair_address, network, decimals, symbol,
                 status, source, audit_done)
            VALUES (%s, %s, %s, %s, %s, %s, 'strat', TRUE) AS new
            ON DUPLICATE KEY UPDATE
                status = new.status,
                symbol = new.symbol,
                audit_done = TRUE
            """,
            (target_address, pair_address, network, 18, symbol, status),
        )

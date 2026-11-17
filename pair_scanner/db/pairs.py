from config import BOT_SETTINGS
from config.scanner import PAIR_RETRY_ALWAYS, PAIR_RETRY_LIMITED, PAIR_STATUS

from . import db_session


async def pairs_add(target_address, pair_address, network, decimals=18):
    async with db_session() as cur:
        await cur.execute(
            """
            INSERT IGNORE INTO pairs
            (target_address, pair_address, network, decimals, status)
            VALUES (%s, %s, %s, %s, %s)
        """,
            (
                target_address,
                pair_address,
                network,
                decimals,
                "pending",
            ),
        )
        return True


async def pairs_update_metadata(address, symbol, decimals):
    async with db_session() as cur:
        await cur.execute(
            """
            UPDATE pairs
            SET symbol = %s, decimals = %s, status = 'awaiting'
            WHERE target_address = %s
        """,
            (symbol, decimals, address),
        )


async def pairs_mark_as_invalid(address):
    async with db_session() as cur:
        await cur.execute(
            "UPDATE pairs SET status = 'invalid' WHERE target_address = %s", (address,)
        )


async def pairs_get_for_audit():
    cooldown = float(BOT_SETTINGS.get("audit_cooldown", 120))
    audit_limit = int(BOT_SETTINGS.get("auto_audit_max", 20))
    max_retries = int(BOT_SETTINGS.get("max_audits", 3))

    async with db_session() as cur:
        statuses = ["awaiting"] + PAIR_RETRY_LIMITED
        placeholders = ",".join(["%s"] * len(statuses))

        await cur.execute(
            f"""
            SELECT id, target_address, pair_address, network, symbol, decimals
            FROM pairs
            WHERE status IN ({placeholders})
            AND source = 'pairs'
            AND found_at <= (NOW() - INTERVAL %s MINUTE)
            AND retry_count < %s
            ORDER BY found_at ASC
            LIMIT %s
        """,
            (*statuses, cooldown, max_retries, audit_limit),
        )
        res = await cur.fetchall()

        always_placeholders = ",".join(["%s"] * len(PAIR_RETRY_ALWAYS))
        await cur.execute(
            f"""
            SELECT id, target_address, pair_address, network, symbol, decimals
            FROM pairs
            WHERE status IN ({always_placeholders})
            AND source = 'pairs'
            AND found_at <= (NOW() - INTERVAL %s MINUTE)
            ORDER BY found_at ASC
            LIMIT %s
        """,
            (*PAIR_RETRY_ALWAYS, cooldown, audit_limit),
        )
        always_res = await cur.fetchall()

        combined = list(res or []) + list(always_res or [])
        return combined if combined else []


async def pairs_get_rejection_counts():
    async with db_session() as cur:
        placeholders = ",".join(["%s"] * len(PAIR_STATUS))
        await cur.execute(
            f"SELECT status, COUNT(*) as count FROM pairs WHERE status IN ({placeholders}) GROUP BY status",
            tuple(PAIR_STATUS),
        )
        rows = await cur.fetchall()
        return {row["status"]: row["count"] for row in rows} if rows else {}


async def pairs_mark_audit_complete(address, final_status):
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


async def pairs_reset_db():
    async with db_session() as cur:
        await cur.execute("TRUNCATE TABLE pairs")
        await cur.execute("TRUNCATE TABLE positions")


async def pairs_get_status_count(status, table):
    async with db_session() as cur:
        await cur.execute(
            f"SELECT COUNT(*) as cnt FROM {table} WHERE status = %s", (status,)
        )
        res = await cur.fetchone()
        return int(res["cnt"] or 0) if res else 0


async def pairs_get_by_id(row_id, table_name):
    async with db_session() as cur:
        await cur.execute(f"SELECT * FROM {table_name} WHERE id = %s", (row_id,))
        return await cur.fetchone()


async def pairs_get_by_status(status, table, limit=None):
    async with db_session() as cur:
        query = f"SELECT * FROM {table}"
        params = []
        if status:
            query += " WHERE status = %s"
            params.append(status.lower())
        query += " ORDER BY id DESC"
        if limit:
            query += " LIMIT %s"
            params.append(limit)
        await cur.execute(query, params)
        return await cur.fetchall()


async def pairs_get_stats(days=1):
    stats_dict = {
        "total": 0,
        "pending": 0,
        "awaiting": 0,
        "monitoring": 0,
        "completed": 0,
        **{status: 0 for status in PAIR_STATUS},
        "pulse_total": 0,
        "pulse_passed": 0,
        "pulse_completed": 0,
        "pulse_rejected": 0,
        "pulse_closed": 0,
        "pulse_avg_pnl": 0.0,
        "pulse_wins": 0,
        "pulse_eth_pnl": 0.0,
        "pulse_eth_counted": 0,
        "active_positions": 0,
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
                COUNT(*) AS total,
                SUM(CASE WHEN status IN ({rejection_placeholders}) THEN 1 ELSE 0 END) AS rejected
            FROM pairs
            WHERE {window.format(col="found_at")}
            """,
            (*PAIR_STATUS, *window_args),
        )
        res = await cur.fetchone()
        if res:
            stats_dict["pulse_total"] = int(res["total"] or 0)
            stats_dict["pulse_rejected"] = int(res["rejected"] or 0)

        await cur.execute(
            f"SELECT COUNT(*) AS bought FROM positions WHERE {window.format(col='tracked_at')}",
            window_args,
        )
        stats_dict["pulse_passed"] = int((await cur.fetchone())["bought"] or 0)

        await cur.execute(
            f"""
            SELECT
                COUNT(*)                                         AS closed,
                AVG(pnl_percent)                                 AS avg_pnl,
                SUM(CASE WHEN pnl_percent > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(eth_received - eth_spent)                    AS eth_pnl,
                COUNT(eth_received - eth_spent)                  AS eth_counted
            FROM positions
            WHERE status = 'completed'
              AND {window.format(col="last_updated")}
            """,
            window_args,
        )
        pnl_res = await cur.fetchone()
        if pnl_res:
            stats_dict["pulse_completed"] = int(pnl_res["closed"] or 0)
            stats_dict["pulse_closed"] = int(pnl_res["closed"] or 0)
            stats_dict["pulse_avg_pnl"] = float(pnl_res["avg_pnl"] or 0)
            stats_dict["pulse_wins"] = int(pnl_res["wins"] or 0)
            stats_dict["pulse_eth_pnl"] = float(pnl_res.get("eth_pnl") or 0)
            stats_dict["pulse_eth_counted"] = int(pnl_res.get("eth_counted") or 0)

        await cur.execute(
            "SELECT COUNT(*) as active FROM positions WHERE status = 'monitoring'"
        )
        pos_res = await cur.fetchone()
        stats_dict["active_positions"] = int(pos_res["active"] or 0) if pos_res else 0

        await cur.execute("""
            SELECT id, target_address, symbol, pnl_percent, tracked_at as found_at, status
            FROM positions
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


async def pairs_position_add(entry, metrics, sec_status, is_renounced, gp_data=None):
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
             buy_tax, sell_tax)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            ),
        )
        return cur.lastrowid


async def pairs_position_update(
    metrics, pnl, liq_drift, sec_status, is_renounced, pos_id, gp_data=None
):
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
                pos_id,
            ),
        )


async def pairs_mark_completed(
    address, pnl=None, sell_tx=None, eth_spent=None, eth_received=None
):
    async with db_session() as cur:
        await cur.execute(
            "UPDATE pairs SET status = 'completed' WHERE target_address = %s",
            (address,),
        )
        await cur.execute(
            "UPDATE positions SET status = 'completed', "
            "pnl_percent = COALESCE(%s, pnl_percent), "
            "sell_tx = COALESCE(%s, sell_tx), "
            "eth_spent = COALESCE(%s, eth_spent), "
            "eth_received = COALESCE(%s, eth_received) "
            "WHERE target_address = %s",
            (pnl, sell_tx, eth_spent, eth_received, address),
        )


async def pairs_mark_for_retry(address):
    async with db_session() as cur:
        await cur.execute(
            """
            UPDATE pairs
            SET retry_count = retry_count + 1, status = 'awaiting'
            WHERE target_address = %s
            """,
            (address,),
        )


async def pairs_strat_add(target_address, pair_address, symbol, status, network="Base"):
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

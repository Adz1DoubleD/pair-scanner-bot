import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import aiomysql
import sentry_sdk

from ..settings import SETTINGS

DB_POOL_SIZE = 5
DB_CONNECT_TIMEOUT = 10
DB_POOL_RECYCLE = 280

_pool = None
_pool_lock = asyncio.Lock()


async def get_db_pool():
    global _pool
    async with _pool_lock:
        if _pool is None:
            if not SETTINGS.db_url:
                raise RuntimeError(f"{SETTINGS.env_prefix}DB_URL is not set")
            url = urlparse(SETTINGS.db_url)
            _pool = await aiomysql.create_pool(
                host=url.hostname,
                user=url.username,
                password=url.password,
                port=url.port or 3306,
                db=url.path[1:],
                autocommit=True,
                minsize=1,
                maxsize=DB_POOL_SIZE,
                connect_timeout=DB_CONNECT_TIMEOUT,
                pool_recycle=DB_POOL_RECYCLE,
            )
    return _pool


@asynccontextmanager
async def db_session():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            try:
                yield cur
            except Exception as e:
                sentry_sdk.capture_exception(e)
                raise


async def close_db():
    global _pool
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None


async def get_neighbours(curr_id: int, table: str, order_by: str = "id"):
    async with db_session() as cur:
        await cur.execute(
            f"""
            SELECT
                (SELECT id FROM `{table}` WHERE `{order_by}` < c.`{order_by}`
                 ORDER BY `{order_by}` DESC LIMIT 1) AS prev_id,
                (SELECT id FROM `{table}` WHERE `{order_by}` > c.`{order_by}`
                 ORDER BY `{order_by}` ASC LIMIT 1) AS next_id
            FROM `{table}` c
            WHERE c.id = %s
        """,
            (curr_id,),
        )
        row = await cur.fetchone()
        return (row["prev_id"], row["next_id"]) if row else (None, None)

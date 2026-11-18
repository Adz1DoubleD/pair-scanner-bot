from ..settings import OPTION_DEFAULTS, OPTIONS, option_value
from .core import db_session


async def option_set(name, value):
    if name not in OPTION_DEFAULTS:
        raise ValueError(f"Unknown scanner setting: {name}")
    value = option_value(name, value)
    async with db_session() as cur:
        await cur.execute(
            "INSERT INTO scanner_settings (name, value) VALUES (%s, %s) AS new "
            "ON DUPLICATE KEY UPDATE value = new.value",
            (name, str(value)),
        )
    OPTIONS[name] = value
    return value

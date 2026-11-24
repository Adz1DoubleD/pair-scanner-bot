"""Small formatting and HTTP helpers shared across the scanner."""

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import sentry_sdk
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .settings import SETTINGS

GECKO_POOLS = "https://api.geckoterminal.com/api/v2/networks/base/pools"


def escape_markdown(text: object) -> str:
    escape_chars = r"_*[`"
    shown = re.sub(r"(alchemy\.com/v2/|ankr\.com/\w+/)[\w-]+", r"\1<hidden>", str(text))
    return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", shown)


def to_float(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def close_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Close", callback_data="cancel")]]
    )


def format_balance(amount: float) -> str:
    if amount >= 1_000_000_000:
        return f"{amount / 1_000_000_000:.2f}B"
    elif amount >= 1_000_000:
        return f"{amount / 1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"{amount / 1_000:.2f}K"
    else:
        return f"{amount:,.4f}"


def format_duration(total_mins: float) -> str:
    if not total_mins:
        return "0m"
    hours, mins = divmod(total_mins, 60)
    return f"{hours}h {mins}m" if hours > 0 else f"{mins}m"


def format_timestamp(date_val: Any) -> tuple[str, str]:
    if not date_val:
        return "???", "Never"

    local_tz = ZoneInfo(SETTINGS.timezone)
    now_local = datetime.now(local_tz)

    if not date_val.tzinfo:
        date_val = date_val.replace(tzinfo=ZoneInfo("UTC"))

    date_local = date_val.astimezone(local_tz)

    diff_days = (now_local.date() - date_local.date()).days
    time_str = date_local.strftime("%H:%M")

    if diff_days <= 0:
        date_str = f"{time_str} Today"
    elif diff_days == 1:
        date_str = f"{time_str} Yesterday"
    elif diff_days < 7:
        date_str = f"{time_str} {date_local.strftime('%a')}"
    else:
        date_str = f"{time_str} {date_local.strftime('%d/%m')}"

    diff = now_local - date_local
    days = diff.days
    hours, remainder = divmod(diff.seconds, 3600)
    minutes, _ = divmod(remainder, 60)

    if days == 1:
        age_str = "1 day ago"
    elif days > 1:
        age_str = f"{days} days ago"
    elif hours > 0:
        age_str = f"{hours}h {minutes}m ago"
    else:
        age_str = f"{minutes}m ago"

    return date_str, age_str


async def fetch(
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = 5,
) -> Any:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                if response.status == 200:
                    res = await response.json()
                    return (
                        res if isinstance(res, dict) else {"value": res, "status": 200}
                    )

                return {"error": True, "status": response.status}

    except Exception as e:
        sentry_sdk.capture_exception(e)
        return {"error": True, "status": 999, "message": str(e)}

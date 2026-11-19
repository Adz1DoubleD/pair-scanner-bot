"""The wallet balances screen."""

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..networks import NETWORKS
from ..settings import SETTINGS
from ..w3.tools import eth_balance_delta, fetch_eth_usd_price, fetch_network_bal


async def balances_block(brief: bool = False) -> dict:
    """The wallet's ETH balance on each network as a block."""
    eth_usd = await fetch_eth_usd_price()

    tasks = [
        fetch_network_bal(n, c, SETTINGS.wallet_address) for n, c in NETWORKS.items()
    ]
    results = await asyncio.gather(*tasks)

    total_eth = 0.0
    buttons = []
    body = ""
    for name, eth_val in results:
        if eth_val is not None:
            usd_val = eth_val * eth_usd
            total_eth += eth_val
            body += f" ├ {name}: {eth_val:.4f} ETH (${usd_val:.2f})\n"
            conf = NETWORKS.get(name, {})
            base = conf.get("scan", "").replace("/tx/", "")
            buttons.append(
                InlineKeyboardButton(
                    f"View {name}", url=f"{base}/address/{SETTINGS.wallet_address}"
                )
            )
        else:
            body += f" ├ {name}: ⚠️ Connection Error\n"

    total_usd = total_eth * eth_usd
    body += f" └ *Total: {total_eth:.4f} ETH (${total_usd:.2f})*\n"

    delta = await eth_balance_delta(total_eth)
    delta_str = f" ({delta:+.2f}% 24h)" if delta is not None else ""

    text = f" ₿ *Crypto*{delta_str}\n" + body + "\n"

    kb = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]

    kb.append([InlineKeyboardButton("🔄 Refresh", callback_data="scanner:balances")])
    kb.append([InlineKeyboardButton("❌ Close", callback_data="cancel")])

    return {
        "text": text,
        "reply_markup": InlineKeyboardMarkup(kb),
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

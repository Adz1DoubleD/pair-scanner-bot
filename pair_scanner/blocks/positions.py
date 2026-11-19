"""Screens for positions: lists, details and manual sells."""

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from web3 import Web3

from ..db.core import get_neighbours
from ..db.pairs import (
    pairs_get_by_id,
    pairs_get_by_status,
    pairs_mark_completed,
    pairs_position_update,
)
from ..helpers import (
    escape_markdown,
    format_balance,
)
from ..networks import ERC20_ABI, NETWORKS
from ..settings import SETTINGS
from ..w3 import open_w3
from ..w3.audit import get_security_audit, get_token_metrics
from ..w3.tools import (
    fetch_eth_usd_price,
    trade_result,
)
from ..w3.trade import execute_sell


async def block_detail(
    context: ContextTypes.DEFAULT_TYPE | None, row_id: int | str, table: str
) -> dict[str, Any]:
    data = await pairs_get_by_id(row_id, "positions")
    if not data:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Back ", callback_data="scanner:dashboard")]]
        )
        return {
            "text": "❌ Position not found.",
            "reply_markup": kb,
        }

    prev_id, next_id = await get_neighbours(int(row_id), "positions")

    try:
        pnl = float(data.get("pnl_percent") or 0)
        liq_drift = float(data.get("liq_drift_percent") or 0)
        peak_pnl = float(data.get("peak_pnl_percent") or 0)
    except (ValueError, TypeError):
        pnl, liq_drift, peak_pnl = 0.0, 0.0, 0.0

    pnl_emoji = "🚀" if pnl >= 0 else "📉"
    current_p = float(data.get("current_price_eth") or 0)
    current_status = data.get("status", "").lower()
    conf = NETWORKS[data.get("network") or "Base"]
    token_addr = data["target_address"]
    dex = conf["dex"]

    balance_line = ""
    metrics = {"success": False}
    sec_status, is_renounced, gp_data = None, False, None
    live_append = ""

    if current_status == "monitoring":
        try:
            decimals = data.get("decimals", 18)
            w3 = await open_w3(conf)
            token_contract = w3.eth.contract(
                address=Web3.to_checksum_address(token_addr),
                abi=ERC20_ABI,
            )
            raw_balance = await token_contract.functions.balanceOf(
                SETTINGS.wallet_address
            ).call()
            token_balance = raw_balance / 10**decimals
            eth_usd = await fetch_eth_usd_price()
            usd_value = token_balance * current_p * eth_usd
            balance_line = (
                f"🪙 *Held:* `{format_balance(token_balance)}` (${usd_value:.2f})\n"
            )
        except Exception:
            balance_line = ""

        metrics = await get_token_metrics(data, conf)
        sec_status, is_renounced, gp_data = await get_security_audit(
            token_addr, conf["chain_id"]
        )

        if metrics["success"]:
            try:
                r = float(metrics["ratio"])
                liq_val = float(metrics["liq_eth"])
                mcap_val = float(metrics["mcap_eth"])
            except (ValueError, TypeError):
                r, liq_val, mcap_val = 0.0, 0.0, 0.0

            r_display = round(r, 3) if r < 0.1 else round(r, 1)
            r_display = min(r_display, 100.0)

            if liq_val < 0.001:
                ratio_emoji = "🔴"
                health_note = " (Dust)"
            else:
                ratio_emoji = "🔴" if r < 2 else "🟡" if r < 10 else "🟢"
                health_note = ""

            buy_tax = gp_data.get("buy_tax") if gp_data else None
            sell_tax = gp_data.get("sell_tax") if gp_data else None
            tax_line = (
                f"💸 *Tax:* `Buy {buy_tax:.0%} / Sell {sell_tax:.0%}`\n"
                if buy_tax is not None and sell_tax is not None
                else "💸 *Tax:* `Unknown`\n"
            )

            live_append = (
                f"\n💧 *Liquidity:* `{liq_val:.4f} ETH`\n"
                f"💰 *MCap:* `{mcap_val:.2f} ETH`\n"
                f"📊 *Ratio:* {ratio_emoji} `{r_display}%`{health_note}\n"
                f"🛡️ *Security:* `{sec_status.capitalize()}`\n"
                f"{tax_line}"
                f"🔐 *Renounced:* `{'Yes' if is_renounced else 'No'}`\n"
            )

    spent, received = data.get("eth_spent"), data.get("eth_received")
    trade_line = (
        f"💵 *Spent / Returned:* `{float(spent):.8f}` / `{float(received):.8f} ETH`\n"
        if spent is not None and received is not None
        else ""
    )

    msg = (
        f"💰 *Position Details: #{row_id}* (${escape_markdown(data.get('symbol') or '???')})\n\n"
        f"🌐 *Network:* {data.get('network') or 'Base'}\n"
        f"🚦 *Status:* `{data.get('status', 'N/A').upper()}`\n"
        f"{pnl_emoji} *PnL:* `{pnl:+.2f}%`\n"
        f"🏆 *Peak PnL:* `{peak_pnl:+.2f}%`\n"
        f"💧 *Liq Drift:* `{liq_drift:+.2f}%`\n"
        f"{trade_line}"
        f"{balance_line}"
        f"{live_append}"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "📊 Chart",
                url=f"{dex}{data.get('pair_address')}",
            )
        ],
    ]

    tx_row = [
        InlineKeyboardButton(
            label, url=f"{conf['scan']}0x{data[key].removeprefix('0x')}"
        )
        for key, label in (("buy_tx", "🧾 Buy Tx"), ("sell_tx", "🧾 Sell Tx"))
        if data.get(key)
    ]
    if tx_row:
        keyboard.append(tx_row)

    if current_status == "monitoring" and data.get("buy_tx"):
        keyboard.append(
            [
                InlineKeyboardButton(
                    "💰 Sell Position", callback_data=f"scanner:sell:{row_id}"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "🗑️ Remove Position",
                callback_data=f"scanner:remove_confirm:{table}:{row_id}",
            )
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                f"🔙 Back to {data.get('status', 'List').replace('-', ' ').title()}",
                callback_data=f"scanner:p-status:{data.get('status')}",
            )
        ]
    )

    nav_row = []
    if prev_id:
        nav_row.append(
            InlineKeyboardButton(
                f"⬅️ {prev_id}", callback_data=f"scanner:p-status:{prev_id}"
            )
        )
    else:
        nav_row.append(InlineKeyboardButton(" ", callback_data="ignore"))

    if current_status == "monitoring":
        nav_row.append(
            InlineKeyboardButton(
                "🔄 Refresh", callback_data=f"scanner:p-status:{row_id}"
            )
        )
    else:
        nav_row.append(InlineKeyboardButton(" ", callback_data="ignore"))

    if next_id:
        nav_row.append(
            InlineKeyboardButton(
                f"{next_id} ➡️", callback_data=f"scanner:p-status:{next_id}"
            )
        )
    else:
        nav_row.append(InlineKeyboardButton(" ", callback_data="ignore"))

    keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="cancel")])

    return {"text": msg, "reply_markup": InlineKeyboardMarkup(keyboard)}


async def block_list(
    context: ContextTypes.DEFAULT_TYPE | None, status_arg: str
) -> dict[str, Any]:
    parts = status_arg.split(":")
    status_input = parts[0].lower()
    current_page = int(parts[1]) if len(parts) > 1 else 0

    found_positions = await pairs_get_by_status(status=status_input, table="positions")
    if not found_positions:
        return {
            "text": f"No positions found with status: {status_input.upper()}",
            "reply_markup": InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔙 Back", callback_data="scanner:dashboard"
                        )
                    ],
                    [InlineKeyboardButton("❌ Close", callback_data="cancel")],
                ]
            ),
        }

    page_size = 10
    total_count = len(found_positions)
    total_pages = (total_count + page_size - 1) // page_size
    chunk = found_positions[current_page * page_size : (current_page + 1) * page_size]

    details = []
    id_buttons = []
    for p in chunk:
        if not isinstance(p, dict):
            continue
        rid = p.get("id", "?")
        sym = escape_markdown(p.get("symbol") or "???")
        try:
            pnl = float(p.get("pnl_percent") or 0)
        except (ValueError, TypeError):
            pnl = 0.0
        details.append(f"`{rid}` | *{sym}* | `{pnl:+.2f}%`")
        id_buttons.append(
            InlineKeyboardButton(f"{rid}", callback_data=f"scanner:p-status:{rid}")
        )

    header = f"📋 *{status_input.upper()} Positions* ({total_count} total)"
    page_info = f"\n(Page {current_page + 1}/{total_pages})\n"
    response_text = header + page_info + "\n" + "\n".join(details)

    keyboard = []
    nav_row = []
    if current_page > 0:
        nav_row.append(
            InlineKeyboardButton(
                "⬅️ Prev",
                callback_data=f"scanner:p-status:{status_input}:{current_page - 1}",
            )
        )
    if current_page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(
                "Next ➡️",
                callback_data=f"scanner:p-status:{status_input}:{current_page + 1}",
            )
        )
    if nav_row:
        keyboard.append(nav_row)
    for i in range(0, len(id_buttons), 5):
        keyboard.append(id_buttons[i : i + 5])
    keyboard.extend(
        [
            [
                InlineKeyboardButton(
                    "🔙 Back to Scanner", callback_data="scanner:dashboard"
                )
            ],
            [InlineKeyboardButton("❌ Close", callback_data="cancel")],
        ]
    )

    return {
        "text": response_text,
        "reply_markup": InlineKeyboardMarkup(keyboard),
        "parse_mode": "Markdown",
    }


async def button_sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    pos_id = query.data.split(":")[-1]
    pos = await pairs_get_by_id(pos_id, "positions")
    if pos is None:
        await query.answer("That position is gone.", show_alert=True)
        return
    conf = NETWORKS[pos.get("network") or "Base"]
    symbol = escape_markdown(pos.get("symbol") or "???")

    tx_hash, error = await execute_sell(conf, pos)
    if tx_hash:
        trade = await trade_result(
            conf, pos.get("buy_tx"), tx_hash, earlier_eth=pos.get("half_eth")
        )
        await pairs_mark_completed(
            pos["target_address"], sell_tx=tx_hash, **(trade or {})
        )
        result = f"✅ *SELL EXECUTED*: {symbol}"
        if trade and trade["pnl"] is not None:
            result += f" (`{trade['pnl']:+.2f}%`)"
    elif error == "PENDING":
        result = (
            f"⏳ *Sell sent* for {symbol} but not confirmed yet.\n\n"
            "_Position kept open — the monitor closes it once the tokens are gone._"
        )
    else:
        result = (
            f"❌ *Sell failed* for {symbol}\n`{error}`\n\n"
            "_Position kept open — try again or check it on Basescan._"
        )

    block = await block_detail(context, pos_id, "positions")
    await query.edit_message_text(
        f"{block['text']}\n\n{result}",
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=block["reply_markup"],
    )


async def button_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    row_id = parts[2] if len(parts) > 2 else None
    if row_id and row_id.isdigit():
        await _button_refresh(update, context, row_id)
        return

    result = await block_list(context, ":".join(parts[2:]) or "monitoring")
    await query.edit_message_text(
        text=result["text"],
        reply_markup=result["reply_markup"],
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


async def _button_refresh(
    update: Update, context: ContextTypes.DEFAULT_TYPE, row_id: str
) -> None:
    query = update.callback_query
    pos = await pairs_get_by_id(row_id, "positions")
    if not pos or pos.get("status") != "monitoring":
        result = await block_detail(context, row_id, "positions")
        await query.edit_message_text(
            text=result["text"],
            reply_markup=result["reply_markup"],
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return

    network_conf = NETWORKS[pos.get("network") or "Base"]

    new_metrics = await get_token_metrics(pos, network_conf)
    sec_status, is_renounced, gp_data = await get_security_audit(
        pos["target_address"], network_conf["chain_id"]
    )

    entry_price = float(pos.get("entry_price_eth", 0))
    entry_liq = float(pos.get("entry_liq_eth", 0))
    new_price = float(new_metrics.get("price_eth", 0))
    new_liq = float(new_metrics.get("liq_eth", 0))

    if entry_price > 0 and entry_liq > 0:
        pnl = round(((new_price - entry_price) / entry_price * 100), 2)
        liq_change = round(((new_liq - entry_liq) / entry_liq * 100), 2)
        await pairs_position_update(
            new_metrics, pnl, liq_change, sec_status, is_renounced, pos["id"], gp_data
        )

    result = await block_detail(context, row_id, "positions")
    await query.edit_message_text(
        text=result["text"],
        reply_markup=result["reply_markup"],
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )

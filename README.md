# pair-scanner-bot

A self-hosted Telegram bot that watches decentralised exchanges on Base for new token pairs, audits them, and can buy and sell them from **your own wallet**. It runs on its own, or inside another python-telegram-bot application.

> **Use at your own risk.** This is not financial advice. Trading new tokens is extremely risky: most fail, many are scams, and you can lose everything you put in. The software comes with no warranty (see [LICENSE](LICENSE)). Only use a separate wallet holding money you can afford to lose.

**Status:** early work in progress.

## What it does

- **Finds new pairs.** It watches the Uniswap V2, Uniswap V3 and Aerodrome factories on Base for new pools paired with WETH.
- **Audits them.** It checks liquidity, the liquidity-to-market-cap ratio, locked LP, taxes, contract security (GoPlus), honeypot risk (honeypot.is), and whether a sale actually goes through (simulated against the chain).
- **Buys what passes,** if trading is on, up to your limits.
- **Sells** on take-profit, stop-loss, trailing stop, rug (liquidity drop), dead token, or a honeypot appearing later.
- **Strategy monitor (optional):** buys established pools with strong one-hour momentum.
- **Alerts** for every buy, sell, would-buy and health problem.
- **Screens:** the scanner dashboard, pairs, positions, wallet balances, settings and CSV exports.

## Safety

- **Watch-only by default.** A new install finds and audits pairs and tells you what it *would* buy, but never buys. Turning trading on asks you to confirm, and needs `SCANNER_PRIVATE_KEY`.
- **Owner only.** The bot answers only `SCANNER_OWNER_ID`.
- **Your key stays yours.** It's read from your environment, used to sign your own transactions, and never printed, stored or sent anywhere else.
- **Cautious defaults:** 0.001 ETH per buy, at most 3 positions, 5% slippage, and the strategy off.
- **Balance floor.** If the wallet falls below the minimum balance (0.01 ETH by default), the health check switches the monitors off and alerts you.
- **Watch-only stops new buys, not sells.** Positions it already holds keep their stop-losses.

## Run it on its own

You need Python 3.12+, a MySQL database and a bot token from [@BotFather](https://t.me/BotFather).

```bash
pip install pair-scanner-bot
cp .env.example .env        # fill in the required settings
python -m pair_scanner
```

Send `/start` to your bot.

## Settings

| Environment variable | |
|---|---|
| `SCANNER_BOT_TOKEN` | Required. Your bot's token |
| `SCANNER_OWNER_ID` | Required. Your Telegram user ID; the bot answers nobody else |
| `SCANNER_DB_URL` | Required. `mysql://user:pass@host:3306/db`. Tables are created on first start: `pairs`, `positions` and `scanner_settings` |
| `SCANNER_WALLET_ADDRESS` | Required. The wallet it watches and trades from |
| `SCANNER_PRIVATE_KEY` | Only to trade. Without it the bot stays watch-only |
| `SCANNER_ALERTS_CHAT_ID` | Where alerts go. Defaults to your private chat. For a group or channel, add the bot to it first (as an admin, for a channel) |
| `SCANNER_ALCHEMY_API_KEY`, `SCANNER_ANKR_API_KEY` | Optional RPC keys, tried before the public nodes |
| `SCANNER_TIMEZONE` | Default `Europe/London` |
| `SCANNER_SENTRY_DSN` | Optional error reporting when run on its own |

Trading limits, audit thresholds, sell rules and schedules are changed in the bot under **⚙️ Settings** and saved in `scanner_settings`.

## Use it inside another bot

```python
from pair_scanner import start_scanner_bot, stop_scanner_bot

await start_scanner_bot(share_with=application, wallet_address=..., private_key=...)
...
await stop_scanner_bot()
```

Inside a host, `SCANNER_BOT_TOKEN` is optional. Without it, the scanner uses the host's bot: its jobs run on the host's job queue and its alerts come from the host's bot. With it, the scanner runs its own bot alongside the host's. Any setting can be passed as a keyword instead of an environment variable. The host can show the scanner's screens in its own menus, and forward any button whose data starts with `scanner:` to `handle_button`, and text replies to `handle_text_input`:

- `scanner_block(context, brief=False)` is the scanner dashboard; `brief=True` gives a short summary for a daily message.
- `balances_block(brief=False)` shows the wallet's balance on each network.
- `scanner_health()` returns the RPC, balance and monitor status, so a host can fold it into its own health check.

A host that already ran a scanner can hand over its data on the first start. Pass `import_settings_from="settings"`, the name of the host's single-row settings table, and, if the scanner has its own database, `import_from_db_url` with the host's database URL. The scanner then copies, once and in one transaction, the matching settings into `scanner_settings`, and the host's `pairs` and `positions` rows with their original IDs, into its own tables if they're empty. It starts with trading on, since the host was already trading. It only reads from the host's database; the old tables stay there until the host removes them.

## Development

```bash
pip install -e ".[dev]"
pre-commit install
pytest
```

## Licence

[MIT](LICENSE).

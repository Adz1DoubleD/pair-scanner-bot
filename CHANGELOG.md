# Changelog

## 1.0.0 (2026-11-19)

First public release.

### Features

* Watches the Uniswap V2, Uniswap V3 and Aerodrome factories on Base for new pools paired with WETH.
* Audits each pair: liquidity, liquidity-to-market-cap ratio, locked LP, taxes, contract security (GoPlus), honeypot risk (honeypot.is) and a simulated sale.
* Watch-only by default. Buys only after you turn trading on, confirm it, and set a private key.
* Sells on take-profit, stop-loss, trailing stop, a liquidity drop, a dead token, or a honeypot appearing later.
* Optional strategy monitor that buys established pools with strong one-hour momentum.
* Telegram screens for the dashboard, pairs, positions, wallet balances on Base and Ethereum, health, settings, and CSV exports.
* Its own health check: RPCs, wallet balance, database and the pairs feed, with alerts only when something is wrong, and an automatic archive clean-up as the database fills.
* Test mode for trying the screens with no background jobs and no trades.
* Runs on its own, or inside another python-telegram-bot application, on its own bot or the host's.

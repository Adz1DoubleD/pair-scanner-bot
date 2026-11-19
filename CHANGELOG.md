# Changelog

## [1.1.0](https://github.com/Adz1DoubleD/pair-scanner-bot/compare/v1.0.0...v1.1.0) (2026-11-19)


### Features

* a mode for each monitor ([#38](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/38)) ([c714caa](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/c714caa7dd2d6c6a51770553d92861936c9a38ca))
* Off, Watch and Live modes, and health alerts that say it once ([#36](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/36)) ([f5eb528](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/f5eb528ef87b4150ee919d7c9728e0ac8d3dbbe9))

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

# Changelog

## [1.3.0](https://github.com/Adz1DoubleD/pair-scanner-bot/compare/v1.2.0...v1.3.0) (2026-11-19)


### Features

* safer Strategy buys, and a health check for the Strategy side ([#49](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/49)) ([b4cbca7](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/b4cbca7e2ba3ed5515ac329be5f1b202b60a916a))

## [1.2.0](https://github.com/Adz1DoubleD/pair-scanner-bot/compare/v1.1.0...v1.2.0) (2026-11-19)


### Features

* a switch per alert type, and losses shown on their own ([#44](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/44)) ([bc9f856](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/bc9f8566b6e8bd324717a5642bc37c34c1dcf0a2))
* a total for both monitors on the dashboard, and P&L per monitor ([#46](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/46)) ([1dfd29a](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/1dfd29a6fbe7b998e69dce55f00139140e721df6))
* sell half once a position is up 30% ([#42](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/42)) ([2687fcf](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/2687fcf5d5f848f589c3dd09a3d1dcb14a745c7a))
* start the trailing stop earlier, and reject tokens with one big holder ([#40](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/40)) ([140de8b](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/140de8bd8ca64a8070842df78950e3ff86a00484))
* stats per monitor, would-buy counts, and every loss in the P&L ([#45](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/45)) ([dd0ee6c](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/dd0ee6cb07a9b7717947878996eca4b143974642))

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

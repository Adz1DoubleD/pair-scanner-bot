# Changelog

## [1.7.0](https://github.com/Adz1DoubleD/pair-scanner-bot/compare/v1.6.0...v1.7.0) (2026-11-28)


### Features

* reject spam token names ([#84](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/84)) ([ab92205](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/ab922055ee8f17c6e5c931c830033595e7d1287a))

## [1.6.0](https://github.com/Adz1DoubleD/pair-scanner-bot/compare/v1.5.1...v1.6.0) (2026-11-27)


### Features

* watch new Uniswap v4 pools (watch-only) ([#82](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/82)) ([fd7ce43](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/fd7ce432064c4bf25c135e967af8fab9cec4ccde))

## [1.5.1](https://github.com/Adz1DoubleD/pair-scanner-bot/compare/v1.5.0...v1.5.1) (2026-11-27)


### Bug Fixes

* stop calling established tokens honeypots ([#80](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/80)) ([4c1740c](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/4c1740cafc5c8633b332e1cce86f0df1f8e02a3d))

## [1.5.0](https://github.com/Adz1DoubleD/pair-scanner-bot/compare/v1.4.6...v1.5.0) (2026-11-26)


### Features

* clearer health alerts with counts ([#78](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/78)) ([ed3403a](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/ed3403a738c282f747b3922b408b149b56ef036e))

## [1.4.6](https://github.com/Adz1DoubleD/pair-scanner-bot/compare/v1.4.5...v1.4.6) (2026-11-26)


### Bug Fixes

* stop checking pools that have no liquidity ([#76](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/76)) ([9887bb8](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/9887bb8b35286d1887b592b4554c55890d1d570f))

## [1.4.5](https://github.com/Adz1DoubleD/pair-scanner-bot/compare/v1.4.4...v1.4.5) (2026-11-24)


### Bug Fixes

* switch RPC on rate limits, poll for pairs every 30s, hide API keys in alerts, and retry unchecked sells ([#74](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/74)) ([213a50a](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/213a50a7afc5d7959289f8e3987bc1ff4a663b21))

## [1.4.4](https://github.com/Adz1DoubleD/pair-scanner-bot/compare/v1.4.3...v1.4.4) (2026-11-20)


### Bug Fixes

* head the brief dashboard summary '🤖 Token Scanner' ([#68](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/68)) ([33233f9](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/33233f9d555929c4bb28ce7a3e2e1df19de1ca86))
* keep the pairs watcher within free RPC log limits ([#71](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/71)) ([7dda8a7](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/7dda8a79b64a932d44dc949e5bc52164e6b59286))

## [1.4.3](https://github.com/Adz1DoubleD/pair-scanner-bot/compare/v1.4.2...v1.4.3) (2026-11-20)


### Bug Fixes

* (dashboard): refine stat_text handling for brief mode ([d9309c8](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/d9309c809238ca0ae3e87176d0039c0956699809))

## [1.4.2](https://github.com/Adz1DoubleD/pair-scanner-bot/compare/v1.4.1...v1.4.2) (2026-11-20)


### Bug Fixes

* refactor(dashboard): enhance stat_text formatting and brief mode output ([4c7f38c](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/4c7f38c21d8cdc699e6c6cb4505d506814908a06))

## [1.4.1](https://github.com/Adz1DoubleD/pair-scanner-bot/compare/v1.4.0...v1.4.1) (2026-11-20)


### Bug Fixes

* refactor(dashboard): simplify stat_text generation and improve brief mode handling ([062e182](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/062e182003a8e6560ef12a82782287618c9fddbc))

## [1.4.0](https://github.com/Adz1DoubleD/pair-scanner-bot/compare/v1.3.0...v1.4.0) (2026-11-19)


### Features

* document and type the public functions, and add scanner_modes() ([#53](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/53)) ([d7f9cce](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/d7f9cced4b9ecca0590c5de93c401601bc09af6a))
* sell rules use what a sale would really return, and Watch picks are followed ([#51](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/51)) ([7f097d6](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/7f097d6c95a7bfae8a02f8d1693f6955a1381966))
* the dashboard is the scanner's home screen ([#56](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/56)) ([7111a12](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/7111a12740d732bbd06481de418f16ded6cd0ee7))


### Documentation

* describe real sale quotes, followed Watch picks and the Strategy checks ([#57](https://github.com/Adz1DoubleD/pair-scanner-bot/issues/57)) ([cc51d3a](https://github.com/Adz1DoubleD/pair-scanner-bot/commit/cc51d3a44aa463a18084b0012475dfdf81f6a2b3))

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

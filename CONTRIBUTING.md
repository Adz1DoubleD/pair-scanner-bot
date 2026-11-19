# Contributing

Thanks for helping. To get a copy running, follow [Run it on its own](README.md#run-it-on-its-own) in the README. Keep both monitors on 👀 Watch while you test: nothing is bought, and Watch picks are followed as pretend positions so you can see what would have happened.

## Making a change

1. Create a branch from `main`.
2. Make your change, with tests for anything that changes behaviour.
3. Run the checks before pushing:
   ```bash
   pre-commit run --all-files
   pytest
   ```
   `pre-commit` runs Ruff, mypy and the file-size check.
4. Start each commit message with a type, which decides the next version:
   - `fix: ...` for a bug fix (1.0.0 → 1.0.1)
   - `feat: ...` for something new (1.0.0 → 1.1.0)
   - `feat!: ...` or `fix!: ...` for a change hosts must act on (1.0.0 → 2.0.0)
   - `docs:`, `test:`, `ci:`, `chore:` or `refactor:` for anything that doesn't need a release
5. Open a pull request saying what changed and why. CI runs the same checks on Python 3.12, 3.13 and 3.14, and they must pass before merging.

Tests mock the database, the chain and Telegram, so they need no `.env`, network, wallet or MySQL.

## Where things go

The package lives in `src/pair_scanner/`, and the tests run against the installed copy, so install it with `pip install -e ".[dev]"` first.

- `__init__.py`: the public API (`__all__`) and starting the bot.
- `buttons.py` and `text_input.py`: routing button presses and typed replies.
- `blocks/`: the Telegram screens. They edit the current message and give every screen a 🔙 Back button.
- `trading/`: the Pairs, Strategy and position monitors, and the factory watchers.
- `w3/`: everything that talks to the chain: audits, quotes, buys and sells.
- `db/`: the database.

## Adding a button

1. **The callback data** starts with `scanner:` and stays under Telegram's 64-byte limit, IDs included.
2. **The route** goes in `handle_button` in `buttons.py`, or in the screen's own `handle_button` for settings and the dashboard.
3. **Typed input**: set `context.user_data["state"]` and `last_msg_id`, then handle the state in `text_input.py`.
4. **Anything that spends or sells** asks for confirmation first and respects the monitor's mode.
5. **Tests**: test the handler and its callback with made-up data.

## Rules for this repo

- **No keys, wallets or data in the repo.** CI rejects `.env`, key, keystore and data files.
- **Money moves only through `w3/trade.py`**, and only when the monitor's mode allows it. A new way to buy or sell checks `current_mode()` first.
- **Errors go to Sentry**, never to a Telegram chat. Only catch an exception where there is a real fallback, and report it there with `sentry_sdk.capture_exception`.
- **Settings come from `settings.py`.** A new start-up setting gets a default in `SETTINGS`, an environment variable in `ENV_VARS`, a row in the README table and a line in `.env.example`. A new bot setting gets a default in `OPTION_DEFAULTS` and a label and limits in `blocks/settings.py`.

## Code style

- Ruff handles linting and formatting, and mypy checks types (`pre-commit` runs both).
- Every function has type hints. Every file starts with a one-line docstring saying what it's for, and the public functions in `__all__` have a short docstring. Other code has no comments; explain the why in the pull request.
- A leading `_` marks a function used only inside its own file. Public functions come first, then the `_` helpers.
- Keep logic inline where it is used rather than adding small helper functions.
- Files stay under 600 lines (`pre-commit` checks this).

## Releases

Releases are prepared by [release-please](https://github.com/googleapis/release-please):

1. Every merge to `main` updates a "chore(main): release X.Y.Z" pull request. It works out the next version from the commit types since the last release, bumps `version` in `pyproject.toml` and writes `CHANGELOG.md`.
2. When it's time to release, the maintainer merges that pull request.
3. The merge tags `vX.Y.Z`, creates the GitHub release, runs the tests, builds, and waits for the maintainer to approve the `pypi` deployment before uploading to [PyPI](https://pypi.org/project/pair-scanner-bot/).

A version number can only be published once. Hosts pin a version, so nothing reaches a live bot until a host moves to the new release.

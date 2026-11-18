import os
from types import SimpleNamespace

SECRETS = (
    "token",
    "private_key",
    "alchemy_api_key",
    "ankr_api_key",
    "db_url",
    "import_from_db_url",
)

ENV_VARS = {
    "token": "SCANNER_BOT_TOKEN",
    "owner_id": "SCANNER_OWNER_ID",
    "alerts_chat_id": "SCANNER_ALERTS_CHAT_ID",
    "db_url": "SCANNER_DB_URL",
    "wallet_address": "SCANNER_WALLET_ADDRESS",
    "private_key": "SCANNER_PRIVATE_KEY",
    "alchemy_api_key": "SCANNER_ALCHEMY_API_KEY",
    "ankr_api_key": "SCANNER_ANKR_API_KEY",
    "timezone": "SCANNER_TIMEZONE",
    "test_mode": "SCANNER_TEST_MODE",
}


class Settings(SimpleNamespace):
    def __repr__(self):
        shown = {k: ("***" if k in SECRETS and v else v) for k, v in vars(self).items()}
        return f"Settings({shown})"


SETTINGS = Settings(
    token=None,
    owner_id=None,
    alerts_chat_id=None,
    db_url=None,
    wallet_address=None,
    private_key=None,
    alchemy_api_key=None,
    ankr_api_key=None,
    timezone="Europe/London",
    test_mode=False,
    import_settings_from=None,
    import_from_db_url=None,
)

OPTION_DEFAULTS = {
    "trading_enabled": 0,
    "pairs_enabled": 1,
    "strategy_enabled": 0,
    "buy_alerts_enabled": 1,
    "sell_alerts_enabled": 1,
    "buy_amount_eth": 0.001,
    "max_positions": 3,
    "slippage": 5.0,
    "min_eth_balance": 0.01,
    "pairs_monitor_interval": 5,
    "audit_cooldown": 120,
    "min_liquidity_eth": 2.0,
    "min_ratio_percent": 15.0,
    "min_lp_locked_pct": 80,
    "auto_audit_max": 20,
    "max_audits": 3,
    "strategy_monitor_interval": 60,
    "strat_min_liq_usd": 250000.0,
    "strat_min_momentum_h1": 3.0,
    "sell_monitor_interval": 1,
    "take_profit_percent": 100,
    "stop_loss_percent": 25,
    "trailing_stop_percent": 20.0,
    "rug_liq_threshold": 30,
    "dead_token_threshold": 2.0,
    "dead_token_hours": 1.0,
    "health_check_interval": 60,
    "db_cleanup_mb": 4.0,
    "eth_bal_snapshot": 0.0,
    "eth_bal_snapshot_ts": 0.0,
}

OPTIONS = dict(OPTION_DEFAULTS)


def option_value(name, raw):
    default = OPTION_DEFAULTS[name]
    try:
        return type(default)(float(raw))
    except (TypeError, ValueError):
        return default


def setup(**options):
    unknown = set(options) - set(vars(SETTINGS))
    if unknown:
        raise TypeError(f"Unknown scanner settings: {sorted(unknown)}")
    for name, var in ENV_VARS.items():
        value = os.environ.get(var)
        if value:
            setattr(SETTINGS, name, value)
    for name, value in options.items():
        setattr(SETTINGS, name, value)


def is_test_mode():
    return str(SETTINGS.test_mode).lower() in ("1", "true", "yes", "on")


def is_owner(user_id):
    try:
        return SETTINGS.owner_id is not None and int(user_id) == int(SETTINGS.owner_id)
    except (TypeError, ValueError):
        return False

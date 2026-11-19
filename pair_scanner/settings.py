import os
from types import SimpleNamespace

SECRETS = (
    "token",
    "private_key",
    "alchemy_api_key",
    "ankr_api_key",
    "db_url",
)

ENV_VARS = {
    "token": "BOT_TOKEN",
    "owner_id": "OWNER_ID",
    "alerts_chat_id": "ALERTS_CHAT_ID",
    "db_url": "DB_URL",
    "wallet_address": "WALLET_ADDRESS",
    "private_key": "PRIVATE_KEY",
    "alchemy_api_key": "ALCHEMY_API_KEY",
    "ankr_api_key": "ANKR_API_KEY",
    "timezone": "TIMEZONE",
    "mode": "MODE",
    "test_mode": "TEST_MODE",
    "db_dashboard_url": "DB_DASHBOARD_URL",
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
    mode=None,
    test_mode=False,
    env_prefix="",
    db_dashboard_url=None,
)

MODE_OFF, MODE_WATCH, MODE_LIVE = 0, 1, 2
MODE_NAMES = {MODE_OFF: "⏸️ Off", MODE_WATCH: "👀 Watch", MODE_LIVE: "💰 Live"}

OPTION_DEFAULTS = {
    "mode": MODE_WATCH,
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


def setup(env_prefix="", **options):
    unknown = set(options) - set(vars(SETTINGS)) | ({"env_prefix"} & set(options))
    if unknown:
        raise TypeError(f"Unknown scanner settings: {sorted(unknown)}")
    SETTINGS.env_prefix = env_prefix
    for name, var in ENV_VARS.items():
        value = os.environ.get(f"{env_prefix}{var}")
        if value:
            setattr(SETTINGS, name, value)
    for name, value in options.items():
        setattr(SETTINGS, name, value)


def forced_mode():
    if str(SETTINGS.test_mode).lower() in ("1", "true", "yes", "on"):
        return MODE_OFF
    names = {"off": MODE_OFF, "watch": MODE_WATCH, "live": MODE_LIVE}
    return names.get(str(SETTINGS.mode or "").strip().lower())


def current_mode():
    forced = forced_mode()
    if forced is not None:
        return forced
    return int(OPTIONS.get("mode", MODE_WATCH) or 0)


def is_owner(user_id):
    try:
        return SETTINGS.owner_id is not None and int(user_id) == int(SETTINGS.owner_id)
    except (TypeError, ValueError):
        return False

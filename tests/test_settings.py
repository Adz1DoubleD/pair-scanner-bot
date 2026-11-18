import pytest

from pair_scanner import settings
from pair_scanner.settings import OPTION_DEFAULTS, SETTINGS, option_value, setup


def test_printing_settings_never_shows_secrets(monkeypatch):
    monkeypatch.setattr(SETTINGS, "private_key", "0x" + "ab" * 32)
    monkeypatch.setattr(SETTINGS, "token", "123:secret-token")
    monkeypatch.setattr(SETTINGS, "db_url", "mysql://u:p@h/db")
    monkeypatch.setattr(SETTINGS, "alchemy_api_key", "alchemy-secret")

    shown = repr(SETTINGS)

    for secret in ("ab" * 32, "secret-token", "u:p@h", "alchemy-secret"):
        assert secret not in shown
    assert "'private_key': '***'" in shown
    assert SETTINGS.wallet_address in shown


def test_setup_reads_the_environment_then_options(monkeypatch):
    monkeypatch.setenv("OWNER_ID", "42")
    monkeypatch.setenv("TIMEZONE", "UTC")

    setup(timezone="Europe/Paris")

    assert SETTINGS.owner_id == "42"
    assert SETTINGS.timezone == "Europe/Paris"


def test_a_host_prefix_reads_only_the_prefixed_names(monkeypatch):
    monkeypatch.setattr(SETTINGS, "db_url", None)
    monkeypatch.setattr(SETTINGS, "wallet_address", None)
    monkeypatch.setenv("DB_URL", "mysql://host-own/db")
    monkeypatch.setenv("SCANNER_WALLET_ADDRESS", "0xabc")

    setup("SCANNER_")

    assert SETTINGS.db_url is None
    assert SETTINGS.wallet_address == "0xabc"
    assert SETTINGS.env_prefix == "SCANNER_"


def test_setup_rejects_unknown_options():
    with pytest.raises(TypeError, match="wallet"):
        setup(wallet="0x1")


def test_new_installs_start_watch_only_with_cautious_limits():
    assert OPTION_DEFAULTS["trading_enabled"] == 0
    assert OPTION_DEFAULTS["strategy_enabled"] == 0
    assert OPTION_DEFAULTS["buy_amount_eth"] <= 0.001
    assert OPTION_DEFAULTS["max_positions"] <= 3
    assert OPTION_DEFAULTS["min_eth_balance"] > 0


@pytest.mark.parametrize(
    "name,raw,expected",
    [
        ("max_positions", "4", 4),
        ("max_positions", "4.0", 4),
        ("buy_amount_eth", "0.002", 0.002),
        ("trading_enabled", "1", 1),
        ("slippage", "not a number", 5.0),
        ("slippage", None, 5.0),
    ],
)
def test_option_values_keep_their_type(name, raw, expected):
    value = option_value(name, raw)
    assert value == expected
    assert type(value) is type(OPTION_DEFAULTS[name])


@pytest.mark.parametrize("user_id,owner", [(1, True), ("1", True), (2, False)])
def test_only_the_owner_is_the_owner(user_id, owner):
    assert settings.is_owner(user_id) is owner


def test_nobody_is_the_owner_without_an_owner_id(monkeypatch):
    monkeypatch.setattr(SETTINGS, "owner_id", None)
    assert settings.is_owner(1) is False

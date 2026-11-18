import ast
import csv
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import pair_scanner
import pair_scanner.blocks.settings as settings_screen
from pair_scanner.settings import OPTIONS, SETTINGS

OWNER = 1
STRANGER = 9


def press(data, user=OWNER, user_data=None):
    query = SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
        message=SimpleNamespace(chat_id=user, message_id=4, delete=AsyncMock()),
    )
    context = SimpleNamespace(
        user_data=user_data if user_data is not None else {},
        bot=SimpleNamespace(
            send_document=AsyncMock(),
            send_message=AsyncMock(),
            edit_message_text=AsyncMock(),
        ),
    )
    return SimpleNamespace(callback_query=query), context


def callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


@pytest.fixture(autouse=True)
def saved(monkeypatch):
    store = []

    async def option_set(name, value):
        value = type(OPTIONS[name])(float(value))
        store.append((name, value))
        OPTIONS[name] = value
        return value

    monkeypatch.setattr(settings_screen, "option_set", option_set)
    monkeypatch.setattr(settings_screen.jobs, "application", None)
    return store


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data", ["scanner:menu", "scanner:sell_all", "scanner:settings:t:trading_enabled"]
)
async def test_strangers_are_turned_away(data, saved):
    update, context = press(data, user=STRANGER)

    await pair_scanner.handle_button(update, context)

    update.callback_query.answer.assert_awaited_once_with(
        "This bot only answers its owner.", show_alert=True
    )
    update.callback_query.edit_message_text.assert_not_awaited()
    assert saved == []


@pytest.mark.asyncio
async def test_home_menu_shows_watch_only(monkeypatch):
    OPTIONS["trading_enabled"] = 0
    update, context = press("scanner:menu")

    await pair_scanner.handle_button(update, context)

    args = update.callback_query.edit_message_text.await_args
    assert "Watch-only" in args.args[0]
    assert callbacks(args.kwargs["reply_markup"]) == [
        "scanner:home",
        "scanner:balances",
        "scanner:settings",
        "cancel",
    ]


@pytest.mark.asyncio
async def test_close_deletes_the_message():
    update, context = press("cancel")

    await pair_scanner.handle_button(update, context)

    update.callback_query.message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_export_sends_a_csv(monkeypatch):
    rows = [{"id": 1, "symbol": "AAA"}, {"id": 2, "symbol": "BBB"}]
    monkeypatch.setattr(
        pair_scanner, "pairs_get_by_status", AsyncMock(return_value=rows)
    )
    update, context = press("scanner:export:positions")

    await pair_scanner.handle_button(update, context)

    sent = context.bot.send_document.await_args.kwargs
    assert sent["filename"].startswith("positions_")
    lines = list(csv.reader(io.StringIO(sent["document"].read().decode("utf-8-sig"))))
    assert lines == [["id", "symbol"], ["1", "AAA"], ["2", "BBB"]]
    pair_scanner.pairs_get_by_status.assert_awaited_once_with(None, "positions")


@pytest.mark.asyncio
async def test_export_refuses_other_tables(monkeypatch):
    monkeypatch.setattr(pair_scanner, "pairs_get_by_status", AsyncMock())
    update, context = press("scanner:export:scanner_settings")

    await pair_scanner.handle_button(update, context)

    pair_scanner.pairs_get_by_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_turning_trading_on_asks_first(saved):
    OPTIONS["trading_enabled"] = 0
    update, context = press("scanner:settings:t:trading_enabled")

    await pair_scanner.handle_button(update, context)

    args = update.callback_query.edit_message_text.await_args
    assert "Turn trading on?" in args.args[0]
    assert "afford to lose" in args.args[0]
    assert saved == []

    update, context = press("scanner:settings:on:trading")
    await pair_scanner.handle_button(update, context)
    assert saved == [("trading_enabled", 1)]


@pytest.mark.asyncio
async def test_trading_needs_a_private_key(monkeypatch, saved):
    OPTIONS["trading_enabled"] = 0
    monkeypatch.setattr(SETTINGS, "private_key", None)

    for data in ("scanner:settings:t:trading_enabled", "scanner:settings:on:trading"):
        update, context = press(data)
        await pair_scanner.handle_button(update, context)
        assert update.callback_query.answer.await_args.kwargs["show_alert"] is True

    assert saved == []


@pytest.mark.asyncio
async def test_turning_trading_off_needs_no_confirmation(saved):
    OPTIONS["trading_enabled"] = 1
    update, context = press("scanner:settings:t:trading_enabled")

    await pair_scanner.handle_button(update, context)

    assert saved == [("trading_enabled", 0)]


async def type_value(text, user_data):
    update = SimpleNamespace(
        message=SimpleNamespace(text=text, delete=AsyncMock()),
        effective_user=SimpleNamespace(id=OWNER),
        effective_chat=SimpleNamespace(id=OWNER),
    )
    context = SimpleNamespace(
        user_data=user_data,
        bot=SimpleNamespace(edit_message_text=AsyncMock(), send_message=AsyncMock()),
    )
    await pair_scanner.handle_text_input(update, context)
    return context.bot.edit_message_text.await_args.kwargs


@pytest.mark.asyncio
async def test_editing_a_number(saved):
    user_data = {}
    update, context = press("scanner:settings:e:max_positions", user_data=user_data)
    await pair_scanner.handle_button(update, context)
    assert user_data["state"] == "scanner:settings:max_positions"

    shown = await type_value("4", user_data)

    assert saved == [("max_positions", 4)]
    assert shown["text"].startswith("✅ Max Positions set to `4`")
    assert "state" not in user_data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,text", [("slippage", "lots"), ("pairs_monitor_interval", "0")]
)
async def test_bad_values_are_asked_again(saved, name, text):
    user_data = {"state": f"scanner:settings:{name}", "last_msg_id": 4}

    shown = await type_value(text, user_data)

    assert saved == []
    assert shown["text"].startswith("⚠️ That needs to be a number")
    assert user_data["state"] == f"scanner:settings:{name}"


@pytest.mark.asyncio
async def test_text_without_a_settings_prompt_is_ignored(saved):
    shown = SimpleNamespace(
        message=SimpleNamespace(text="5", delete=AsyncMock()),
        effective_user=SimpleNamespace(id=OWNER),
        effective_chat=SimpleNamespace(id=OWNER),
    )
    await pair_scanner.handle_text_input(shown, SimpleNamespace(user_data={}))
    assert saved == []


def test_every_button_except_close_and_blank_starts_with_scanner():
    root = Path(pair_scanner.__file__).parent
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.keyword) and node.arg == "callback_data":
                value = node.value
                text = (
                    value.value
                    if isinstance(value, ast.Constant)
                    else value.values[0].value
                    if isinstance(value, ast.JoinedStr)
                    and isinstance(value.values[0], ast.Constant)
                    else None
                )
                assert (
                    text is None
                    or text in ("cancel", "ignore")
                    or text.startswith("scanner:")
                ), (path, text)


@pytest.mark.asyncio
async def test_start_needs_its_settings(monkeypatch):
    for name in ("token", "db_url", "wallet_address"):
        monkeypatch.setattr(SETTINGS, name, None)
    for var in ("SCANNER_BOT_TOKEN", "SCANNER_DB_URL", "SCANNER_WALLET_ADDRESS"):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ValueError) as e:
        await pair_scanner.start_scanner_bot()

    for var in ("SCANNER_BOT_TOKEN", "SCANNER_DB_URL", "SCANNER_WALLET_ADDRESS"):
        assert var in str(e.value)


def test_the_bot_listens_for_its_buttons_close_and_blank():
    import re

    source = Path(pair_scanner.__file__).read_text(encoding="utf-8")
    pattern = re.search(r'pattern=r"(.+?)"', source).group(1)
    for data in ("scanner:menu", "cancel", "ignore"):
        assert re.match(pattern, data)
    for data in ("cancelled", "back:scanner", "troubleshooter:home"):
        assert not re.match(pattern, data)

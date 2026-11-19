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
    "name,text,message",
    [
        ("slippage", "lots", "Slippage must be between 0 and 50%"),
        ("slippage", "51", "Slippage must be between 0 and 50%"),
        (
            "pairs_monitor_interval",
            "0",
            "Pairs Monitor must be between 1 and 10080 min",
        ),
        ("buy_amount_eth", "100", "Buy Amount must be between 0.00001 and 10 ETH"),
        ("max_positions", "51", "Max Positions must be between 1 and 50"),
        ("strat_min_liq_usd", "-1", "Min Liquidity must be 0 or more USD"),
    ],
)
async def test_bad_values_are_asked_again(saved, name, text, message):
    user_data = {"state": f"scanner:settings:{name}", "last_msg_id": 4}

    shown = await type_value(text, user_data)

    assert saved == []
    assert shown["text"] == f"⚠️ {message}. Send it again:"
    assert user_data["state"] == f"scanner:settings:{name}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,text", [("max_positions", "50"), ("slippage", "0"), ("buy_amount_eth", "10")]
)
async def test_values_at_the_limits_are_accepted(saved, name, text):
    user_data = {"state": f"scanner:settings:{name}", "last_msg_id": 4}

    await type_value(text, user_data)

    assert saved == [(name, type(OPTIONS[name])(float(text)))]


def test_every_number_setting_has_limits():
    numbers = (
        set(settings_screen.LABELS)
        - settings_screen.TOGGLES
        - {"pairs_mode", "strategy_mode"}
    )
    assert numbers == set(settings_screen.LIMITS)


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
    for var in ("BOT_TOKEN", "DB_URL", "WALLET_ADDRESS"):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ValueError) as e:
        await pair_scanner.start_scanner_bot()

    for var in ("BOT_TOKEN", "DB_URL", "WALLET_ADDRESS"):
        assert var in str(e.value)


def test_the_bot_listens_for_its_buttons_close_and_blank():
    import re

    source = Path(pair_scanner.__file__).read_text(encoding="utf-8")
    pattern = re.search(r'pattern=r"(.+?)"', source).group(1)
    for data in ("scanner:menu", "cancel", "ignore"):
        assert re.match(pattern, data)
    for data in ("cancelled", "back:scanner", "troubleshooter:home"):
        assert not re.match(pattern, data)


STATS = {
    "total": 3,
    "monitors": {
        "pairs": {
            "found": 3,
            "rejected": 1,
            "watched": 2,
            "bought": 0,
            "closed": 0,
            "avg_pnl": 0.0,
            "wins": 0,
            "eth_pnl": 0.0,
            "eth_counted": 0,
            "would_closed": 0,
            "would_avg": 0.0,
            "would_wins": 0,
        },
        "strategy": {
            "found": 0,
            "rejected": 0,
            "watched": 0,
            "bought": 0,
            "closed": 0,
            "avg_pnl": 0.0,
            "wins": 0,
            "eth_pnl": 0.0,
            "eth_counted": 0,
            "would_closed": 0,
            "would_avg": 0.0,
            "would_wins": 0,
        },
    },
}


@pytest.mark.asyncio
async def test_dashboard_shows_each_monitor_with_its_mode(monkeypatch):
    import pair_scanner.blocks.scanner as scanner_screen

    OPTIONS.update(pairs_mode=1, strategy_mode=0)
    monkeypatch.setattr(
        scanner_screen,
        "pairs_get_stats",
        AsyncMock(return_value=(STATS, None, None, None)),
    )

    text = (await scanner_screen.handle_block(None, brief=True))["text"]

    assert "🤖 *Pairs* · 👀 Watch" in text
    assert "Would buy: 2" in text
    assert "Strategy" not in text


@pytest.mark.asyncio
async def test_dashboard_links_to_settings_and_back(monkeypatch):
    import pair_scanner.blocks.scanner as scanner_screen

    monkeypatch.setattr(
        scanner_screen,
        "pairs_get_stats",
        AsyncMock(return_value=(STATS, None, None, None)),
    )
    update, context = press("scanner:home")
    await pair_scanner.handle_button(update, context)
    dashboard = update.callback_query.edit_message_text.await_args.kwargs
    assert "scanner:settings" in callbacks(dashboard["reply_markup"])

    update, context = press("scanner:settings")
    await pair_scanner.handle_button(update, context)
    menu = update.callback_query.edit_message_text.await_args.kwargs
    assert callbacks(menu["reply_markup"])[-1] == "scanner:home"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "what,clear_fn", [("archive", "clear_archive"), ("pairs", "pairs_reset_db")]
)
async def test_clearing_asks_first_then_clears(monkeypatch, what, clear_fn):
    import pair_scanner.blocks.scanner as scanner_screen

    cleared = AsyncMock()
    other = AsyncMock()
    monkeypatch.setattr(scanner_screen, clear_fn, cleared)
    monkeypatch.setattr(
        scanner_screen,
        "pairs_reset_db" if clear_fn == "clear_archive" else "clear_archive",
        other,
    )
    monkeypatch.setattr(
        scanner_screen,
        "pairs_get_stats",
        AsyncMock(return_value=({"total": 0}, None, None, None)),
    )

    update, context = press(f"scanner:wipe:{what}")
    await pair_scanner.handle_button(update, context)
    asked = update.callback_query.edit_message_text.await_args
    assert asked.args[0].startswith("⚠️")
    assert callbacks(asked.kwargs["reply_markup"]) == [
        "scanner:archive",
        f"scanner:wiped:{what}",
    ]
    cleared.assert_not_awaited()

    update, context = press(f"scanner:wiped:{what}")
    await pair_scanner.handle_button(update, context)
    cleared.assert_awaited_once()
    other.assert_not_awaited()


@pytest.fixture
def start_env(monkeypatch, jq):
    monkeypatch.setattr(pair_scanner, "init_scanner_db", AsyncMock(return_value={}))
    monkeypatch.setattr(pair_scanner, "refresh_networks", lambda: None)
    monkeypatch.setattr(pair_scanner, "close_db", AsyncMock())
    monkeypatch.setattr(pair_scanner, "close_w3", AsyncMock())
    monkeypatch.setattr(SETTINGS, "db_url", "mysql://u:p@h/db")
    monkeypatch.setattr(SETTINGS, "token", None)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("SCANNER_BOT_TOKEN", raising=False)
    built = []
    monkeypatch.setattr(
        pair_scanner.Application, "builder", lambda: built.append(1) or None
    )
    host = SimpleNamespace(job_queue=jq, bot_data={"host": True})
    yield SimpleNamespace(host=host, jq=jq, built=built)
    pair_scanner.jobs.application = None
    pair_scanner.jobs.shares_host = False


@pytest.mark.asyncio
async def test_inside_a_host_without_a_token_it_uses_the_hosts_bot(start_env):
    OPTIONS.update(pairs_mode=0, strategy_mode=1)
    host_jobs_before = start_env.jq.add_existing("Agenda Message")

    app = await pair_scanner.start_scanner_bot(share_with=start_env.host)

    assert app is start_env.host
    assert start_env.built == []
    assert pair_scanner.jobs.application is start_env.host
    assert {c.name for c in start_env.jq.calls} == {
        "Scanner Health",
        "Sell Monitor",
        "Strategy Monitor",
    }

    await pair_scanner.stop_scanner_bot()

    assert start_env.jq.get_jobs_by_name("Scanner Health") == []
    assert not host_jobs_before.removed
    assert pair_scanner.jobs.application is None


@pytest.mark.asyncio
async def test_inside_a_host_it_reads_scanner_names(start_env, monkeypatch):
    monkeypatch.setattr(SETTINGS, "wallet_address", None)
    monkeypatch.delenv("SCANNER_WALLET_ADDRESS", raising=False)

    with pytest.raises(ValueError, match="SCANNER_WALLET_ADDRESS"):
        await pair_scanner.start_scanner_bot(share_with=start_env.host)


@pytest.mark.asyncio
async def test_on_its_own_it_still_needs_a_token(start_env):
    with pytest.raises(ValueError, match="BOT_TOKEN"):
        await pair_scanner.start_scanner_bot()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "alerts,header",
    [
        ([], "💚 *SCANNER HEALTH OK*"),
        (["🔥 *Base RPC*: all down"], "🚨 *SCANNER HEALTH ALERT*"),
    ],
)
async def test_the_health_button_shows_the_scanners_own_health(
    monkeypatch, alerts, header
):
    import pair_scanner.blocks.scanner as scanner_screen

    monkeypatch.setattr(
        scanner_screen,
        "scanner_health",
        AsyncMock(return_value={"text": "🌐 *Web3*", "alerts": alerts}),
    )
    update, context = press("scanner:health")

    await pair_scanner.handle_button(update, context)

    shown = update.callback_query.edit_message_text.await_args
    assert shown.args[0].startswith(header)
    assert all(a in shown.args[0] for a in alerts)
    assert shown.args[0].endswith("🌐 *Web3*")
    assert callbacks(shown.kwargs["reply_markup"]) == [
        "scanner:health",
        "scanner:home",
        "cancel",
    ]


@pytest.mark.asyncio
async def test_home_menu_shows_each_monitors_mode():
    OPTIONS.update(pairs_mode=2, strategy_mode=1)
    update, context = press("scanner:menu")

    await pair_scanner.handle_button(update, context)

    args = update.callback_query.edit_message_text.await_args
    assert "🤖 Pairs: 💰 Live\n🧭 Strategy: 👀 Watch" in args.args[0]
    assert callbacks(args.kwargs["reply_markup"]) == [
        "scanner:home",
        "scanner:balances",
        "scanner:settings",
        "cancel",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("monitor", ["pairs", "strategy"])
async def test_each_monitor_offers_all_three_modes(monitor):
    OPTIONS.update(pairs_mode=1, strategy_mode=1)
    update, context = press(f"scanner:settings:m:{monitor}")

    await pair_scanner.handle_button(update, context)

    args = update.callback_query.edit_message_text.await_args
    buttons = [b for row in args.kwargs["reply_markup"].inline_keyboard for b in row]
    assert [b.text for b in buttons[:3]] == ["⏸️ Off", "✅ 👀 Watch", "💰 Live"]
    assert buttons[2].callback_data == f"scanner:settings:set:{monitor}:2"


@pytest.mark.asyncio
async def test_the_monitors_screen_shows_a_mode_per_monitor():
    OPTIONS.update(pairs_mode=2, strategy_mode=0)
    update, context = press("scanner:settings:g:monitors")

    await pair_scanner.handle_button(update, context)

    rows = update.callback_query.edit_message_text.await_args.kwargs[
        "reply_markup"
    ].inline_keyboard
    assert [(r[0].text, r[0].callback_data) for r in rows[:2]] == [
        ("🤖 Pairs Monitor: 💰 Live", "scanner:settings:m:pairs"),
        ("🧭 Strategy Monitor: ⏸️ Off", "scanner:settings:m:strategy"),
    ]


@pytest.mark.asyncio
async def test_going_live_asks_first_and_needs_a_key(monkeypatch, saved):
    OPTIONS.update(pairs_mode=1, strategy_mode=1)
    update, context = press("scanner:settings:set:strategy:2")

    await pair_scanner.handle_button(update, context)

    args = update.callback_query.edit_message_text.await_args
    assert "Switch the 🧭 Strategy Monitor to 💰 Live?" in args.args[0]
    assert saved == []

    update, context = press("scanner:settings:on:strategy")
    await pair_scanner.handle_button(update, context)
    assert saved == [("strategy_mode", 2)]
    assert OPTIONS["pairs_mode"] == 1

    monkeypatch.setattr(SETTINGS, "private_key", None)
    for data in ("scanner:settings:set:pairs:2", "scanner:settings:on:pairs"):
        update, context = press(data)
        await pair_scanner.handle_button(update, context)
        assert update.callback_query.answer.await_args.kwargs["show_alert"] is True
    assert saved == [("strategy_mode", 2)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other,target,held,expected",
    [
        (0, 1, 2, "keep selling"),
        (1, 0, 1, "keep selling"),
        (0, 0, 1, "stop until you switch one back on"),
        (0, 1, 0, None),
    ],
)
async def test_changing_a_mode_reminds_you_about_open_positions(
    monkeypatch, saved, other, target, held, expected
):
    OPTIONS.update(pairs_mode=2, strategy_mode=other)
    monkeypatch.setattr(
        settings_screen,
        "pairs_get_by_status",
        AsyncMock(return_value=[{"id": i} for i in range(held)]),
    )
    update, context = press(f"scanner:settings:set:pairs:{target}")

    await pair_scanner.handle_button(update, context)

    assert saved == [("pairs_mode", target)]
    text = update.callback_query.edit_message_text.await_args.args[0]
    if expected:
        assert expected in text and f"You hold {held} open position" in text
    else:
        assert "open position" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("option", [{"mode": "off"}, {"test_mode": True}])
async def test_a_run_forced_off_starts_no_jobs(start_env, capsys, option):
    OPTIONS.update(pairs_mode=2, strategy_mode=2)

    await pair_scanner.start_scanner_bot(share_with=start_env.host, **option)

    assert start_env.jq.calls == []
    assert capsys.readouterr().out.splitlines() == [
        "🚀 Pair scanner running on the host's bot "
        "(Pairs ⏸️ Off, Strategy ⏸️ Off, for this run)"
    ]
    await pair_scanner.stop_scanner_bot()


@pytest.mark.asyncio
async def test_startup_says_which_bot_and_mode(start_env, capsys):
    OPTIONS.update(pairs_mode=0, strategy_mode=1)

    await pair_scanner.start_scanner_bot(share_with=start_env.host)

    assert capsys.readouterr().out.splitlines() == [
        "🚀 Pair scanner running on the host's bot (Pairs ⏸️ Off, Strategy 👀 Watch)"
    ]
    await pair_scanner.stop_scanner_bot()


@pytest.mark.asyncio
async def test_dashboard_adds_a_total_when_both_monitors_show(monkeypatch):
    import pair_scanner.blocks.scanner as scanner_screen

    OPTIONS.update(pairs_mode=2, strategy_mode=1)
    one = {**STATS["monitors"]["pairs"], "bought": 2, "closed": 2, "avg_pnl": 10.0}
    two = {**STATS["monitors"]["pairs"], "bought": 2, "closed": 1, "avg_pnl": -20.0}
    monkeypatch.setattr(
        scanner_screen,
        "pairs_get_stats",
        AsyncMock(
            return_value=(
                {"total": 3, "monitors": {"pairs": one, "strategy": two}},
                None,
                None,
                None,
            )
        ),
    )

    text = (await scanner_screen.handle_block(None, brief=True))["text"]

    total = text.split("📊 *Total*")[1]
    assert "Bought: 4" in total and "Sold: 3" in total
    assert "Avg Return: `+0.00%`" in total


@pytest.mark.asyncio
async def test_dashboard_shows_how_watch_picks_would_have_done(monkeypatch):
    import pair_scanner.blocks.scanner as scanner_screen

    OPTIONS.update(pairs_mode=1, strategy_mode=0)
    pairs = {
        **STATS["monitors"]["pairs"],
        "would_closed": 4,
        "would_avg": 12.5,
        "would_wins": 3,
    }
    monkeypatch.setattr(
        scanner_screen,
        "pairs_get_stats",
        AsyncMock(
            return_value=(
                {"total": 3, "monitors": {**STATS["monitors"], "pairs": pairs}},
                None,
                None,
                None,
            )
        ),
    )

    text = (await scanner_screen.handle_block(None, brief=True))["text"]

    assert "Would have made: `+12.50%` (3/4 up)" in text

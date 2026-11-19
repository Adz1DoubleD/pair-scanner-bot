from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from hexbytes import HexBytes
from web3.exceptions import BadFunctionCallOutput

import pair_scanner.trading.watchers as auto
from pair_scanner.networks import (
    AERO_POOL_CREATED_TOPIC,
    PAIR_CREATED_TOPIC,
    POOL_CREATED_TOPIC,
)

BASE_CONF = {"chain_id": 8453}
WATCH_WETH = "0x4200000000000000000000000000000000000006"
V2_FACTORY = "0x" + "22" * 20
V3_FACTORY = "0x" + "33" * 20
AERO_FACTORY = "0x" + "44" * 20
WATCH_CONF = {
    "weth": WATCH_WETH,
    "v2_factory": V2_FACTORY,
    "v3_factory": V3_FACTORY,
    "aero_factory": AERO_FACTORY,
}
NEW_TOKEN = "0x" + "11" * 20
NEW_POOL = "0x" + "99" * 20


class _StopLoop(BaseException):
    pass


TOKEN = "0x" + "ab" * 20


@pytest.fixture
def meta(monkeypatch):
    m = SimpleNamespace(
        pending=AsyncMock(return_value=[{"target_address": TOKEN, "network": "Base"}]),
        invalid=AsyncMock(),
        update=AsyncMock(),
        symbol=AsyncMock(return_value="TKN"),
        decimals=AsyncMock(return_value=18),
    )
    functions = SimpleNamespace(
        symbol=lambda: SimpleNamespace(call=m.symbol),
        decimals=lambda: SimpleNamespace(call=m.decimals),
    )
    w3 = SimpleNamespace(
        eth=SimpleNamespace(
            contract=lambda address, abi: SimpleNamespace(functions=functions)
        )
    )
    monkeypatch.setattr(auto, "NETWORKS", {"Base": BASE_CONF})
    monkeypatch.setattr(auto, "open_w3", AsyncMock(return_value=w3))
    monkeypatch.setattr(auto, "pairs_get_by_status", m.pending)
    monkeypatch.setattr(auto, "pairs_mark_as_invalid", m.invalid)
    monkeypatch.setattr(auto, "pairs_update_metadata", m.update)

    def passes(n):
        sleep = AsyncMock(side_effect=[None] * (n - 1) + [_StopLoop()])
        monkeypatch.setattr(auto, "asyncio", SimpleNamespace(sleep=sleep))

    m.passes = passes
    return m


async def run_metadata(m, passes=1):
    m.passes(passes)
    with pytest.raises(_StopLoop):
        await auto.watch_pair_metadata()


@pytest.mark.asyncio
async def test_metadata_saves_symbol_and_decimals(meta):
    await run_metadata(meta)

    meta.update.assert_awaited_once_with(TOKEN, "TKN", 18)
    meta.invalid.assert_not_awaited()


@pytest.mark.asyncio
async def test_metadata_marks_non_erc20_invalid(meta):
    meta.symbol.side_effect = BadFunctionCallOutput("no symbol()")

    await run_metadata(meta)

    meta.invalid.assert_awaited_once_with(TOKEN)
    meta.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_metadata_keeps_pair_pending_on_network_error(meta):
    meta.symbol.side_effect = TimeoutError("rpc timeout")

    await run_metadata(meta)

    meta.invalid.assert_not_awaited()
    meta.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_metadata_gives_up_after_repeated_network_errors(meta):
    meta.symbol.side_effect = TimeoutError("rpc timeout")

    await run_metadata(meta, passes=5)

    meta.invalid.assert_awaited_once_with(TOKEN)


def _word(addr):
    return "00" * 12 + addr[2:]


def aero_log(token0, token1, pool, stable=False):
    return {
        "topics": [
            HexBytes(AERO_POOL_CREATED_TOPIC),
            HexBytes("0x" + _word(token0)),
            HexBytes("0x" + _word(token1)),
            HexBytes("0x" + "00" * 31 + ("01" if stable else "00")),
        ],
        "data": HexBytes("0x" + _word(pool) + "00" * 31 + "01"),
    }


class _WatchEth:
    def __init__(self, tip, logs):
        self.tip = tip
        self.get_logs = AsyncMock(return_value=logs)

    @property
    def block_number(self):
        async def tip():
            return self.tip

        return tip()


@pytest.fixture
def watcher(monkeypatch):
    capture = MagicMock()
    added = AsyncMock()
    monkeypatch.setattr(auto, "pairs_add", added)
    monkeypatch.setattr(auto, "sentry_sdk", SimpleNamespace(capture_exception=capture))
    monkeypatch.setattr(
        auto,
        "asyncio",
        SimpleNamespace(
            sleep=AsyncMock(side_effect=_StopLoop()), TimeoutError=TimeoutError
        ),
    )

    async def run(conf=WATCH_CONF, logs=(), tip=100):
        eth = _WatchEth(tip, list(logs))
        monkeypatch.setattr(auto, "NETWORKS", {"Base": conf})
        monkeypatch.setattr(
            auto, "open_w3", AsyncMock(return_value=SimpleNamespace(eth=eth))
        )
        with pytest.raises(_StopLoop):
            await auto.watch_new_pairs()
        capture.assert_not_called()
        return eth.get_logs, added

    return run


@pytest.mark.asyncio
async def test_watcher_queries_uniswap_and_aerodrome_factories(watcher):
    get_logs, _ = await watcher()

    query = get_logs.await_args.args[0]
    assert query["address"] == [V2_FACTORY, V3_FACTORY, AERO_FACTORY]
    assert query["topics"] == [
        [PAIR_CREATED_TOPIC, POOL_CREATED_TOPIC, AERO_POOL_CREATED_TOPIC]
    ]
    assert (query["fromBlock"], query["toBlock"]) == (98, 98)


@pytest.mark.asyncio
async def test_watcher_works_without_an_aerodrome_factory(watcher):
    conf = {k: v for k, v in WATCH_CONF.items() if k != "aero_factory"}

    get_logs, _ = await watcher(conf=conf)

    assert get_logs.await_args.args[0]["address"] == [V2_FACTORY, V3_FACTORY]


@pytest.mark.asyncio
@pytest.mark.parametrize("stable", [False, True])
async def test_watcher_adds_new_aerodrome_weth_pools(watcher, stable):
    _, added = await watcher(logs=[aero_log(NEW_TOKEN, WATCH_WETH, NEW_POOL, stable)])

    added.assert_awaited_once_with(NEW_TOKEN, NEW_POOL, "Base")


@pytest.mark.asyncio
async def test_watcher_ignores_aerodrome_pools_without_weth(watcher):
    other = "0x" + "55" * 20

    _, added = await watcher(logs=[aero_log(NEW_TOKEN, other, NEW_POOL)])

    added.assert_not_awaited()

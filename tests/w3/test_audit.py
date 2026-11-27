import asyncio
from unittest.mock import AsyncMock

import pytest
from eth_utils import keccak, to_hex
from hexbytes import HexBytes
from web3 import Web3

import pair_scanner.w3 as w3_core
import pair_scanner.w3.audit as w3_audit
from conftest import WETH, addr_word, base_urls, try_urls
from pair_scanner.networks import (
    AERO_POOL_CREATED_TOPIC,
    NETWORKS,
    PAIR_CREATED_TOPIC,
    POOL_CREATED_TOPIC,
)
from pair_scanner.w3.audit import parse_pair_created


def gecko_pool(addr, base, quote, name):
    return {
        "attributes": {
            "address": addr,
            "name": name,
            "reserve_in_usd": "500000",
            "price_change_percentage": {"h1": "5"},
        },
        "relationships": {
            "base_token": {"data": {"id": f"base_{base}"}},
            "quote_token": {"data": {"id": f"base_{quote}"}},
        },
    }


def _make_pair_log(token0, token1, pair):
    data = HexBytes("0x" + addr_word(pair) + "00" * 31 + "01")
    return {
        "topics": [
            HexBytes("0x" + "de" * 32),
            HexBytes("0x" + addr_word(token0)),
            HexBytes("0x" + addr_word(token1)),
        ],
        "data": data,
    }


AERO_POOL_BLOCK = 16_034_121


AERO_POOL_ADDR = "0xac9640796fCD47eD9e84AE4ca9a25577F891f40E"


def test_parse_pair_created_roundtrip():
    token0 = "0x4200000000000000000000000000000000000006"
    token1 = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    pair = "0x1234567890abcdef1234567890ABCDEF12345678"
    t0, t1, p = parse_pair_created(_make_pair_log(token0, token1, pair))
    assert t0.lower() == token0.lower()
    assert t1.lower() == token1.lower()
    assert p.lower() == pair.lower()


def test_parse_pair_created_outputs_valid_addresses():
    log = _make_pair_log(
        "0x4200000000000000000000000000000000000006",
        "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )
    for a in parse_pair_created(log):
        assert a.startswith("0x") and len(a) == 42


def test_parse_pair_created_reads_v3_pool_created():
    token0 = "0x4200000000000000000000000000000000000006"
    token1 = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    pool = "0x1234567890abcdef1234567890ABCDEF12345678"
    log = {
        "topics": [
            HexBytes(POOL_CREATED_TOPIC),
            HexBytes("0x" + addr_word(token0)),
            HexBytes("0x" + addr_word(token1)),
            HexBytes("0x" + "00" * 29 + "000bb8"),
        ],
        "data": HexBytes("0x" + "00" * 31 + "3c" + addr_word(pool)),
    }

    t0, t1, p = parse_pair_created(log)

    assert (t0.lower(), t1.lower(), p.lower()) == (
        token0.lower(),
        token1.lower(),
        pool.lower(),
    )


def test_parse_pair_created_reads_aerodrome_pool_created():
    token0 = "0x4200000000000000000000000000000000000006"
    token1 = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    pool = "0x1234567890abcdef1234567890ABCDEF12345678"
    log = {
        "topics": [
            HexBytes(AERO_POOL_CREATED_TOPIC),
            HexBytes("0x" + addr_word(token0)),
            HexBytes("0x" + addr_word(token1)),
            HexBytes("0x" + "00" * 31 + "01"),
        ],
        "data": HexBytes("0x" + addr_word(pool) + "00" * 31 + "07"),
    }

    t0, t1, p = parse_pair_created(log)

    assert (t0.lower(), t1.lower(), p.lower()) == (
        token0.lower(),
        token1.lower(),
        pool.lower(),
    )


def test_aero_pool_created_topic_matches_signature():
    signature = "PoolCreated(address,address,bool,address,uint256)"
    assert to_hex(keccak(text=signature)) == AERO_POOL_CREATED_TOPIC


def test_live_pair_created_decode():
    conf, urls = base_urls()

    async def scan(w3):
        tip = (await w3.eth.block_number) - 5
        for i in range(10):
            to_block = tip - i * 1000
            found = await w3.eth.get_logs(
                {
                    "fromBlock": to_block - 999,
                    "toBlock": to_block,
                    "address": conf["v2_factory"],
                    "topics": [PAIR_CREATED_TOPIC],
                }
            )
            if found:
                return found
        return []

    try:
        logs = asyncio.run(try_urls(urls, scan))
    except Exception as e:
        pytest.skip(f"no live RPC returned logs ({e})")

    if not logs:
        pytest.skip("no PairCreated events in the last ~10k blocks")

    log = logs[0]
    assert isinstance(log["data"], (bytes, bytearray)), f"data is {type(log['data'])}"
    assert isinstance(log["topics"][1], (bytes, bytearray)), (
        f"topic is {type(log['topics'][1])}"
    )
    t0, t1, pair = parse_pair_created(log)
    for a in (t0, t1, pair):
        assert a.startswith("0x") and len(a) == 42
        Web3.to_checksum_address(a)


def test_live_aero_pool_created_decode(monkeypatch):
    monkeypatch.setattr(w3_core, "aero_pools", {})
    conf, urls = base_urls()

    async def fetch(w3):
        logs = await w3.eth.get_logs(
            {
                "fromBlock": AERO_POOL_BLOCK,
                "toBlock": AERO_POOL_BLOCK,
                "address": conf["aero_factory"],
                "topics": [AERO_POOL_CREATED_TOPIC],
            }
        )
        if not logs:
            return None
        parsed = parse_pair_created(logs[0])
        return logs[0], parsed, await w3_core._aero_stable(w3, conf, parsed[2])

    try:
        result = asyncio.run(try_urls(urls, fetch))
    except Exception as e:
        pytest.skip(f"no live RPC returned Aerodrome logs ({e})")

    assert result is not None, f"no PoolCreated event found at block {AERO_POOL_BLOCK}"

    log, (t0, t1, pool), stable = result
    assert pool.lower() == AERO_POOL_ADDR.lower()
    assert conf["weth"].lower() in (t0.lower(), t1.lower())
    assert stable is False
    assert stable is (bytes(log["topics"][3])[-1] == 1)


@pytest.mark.asyncio
async def test_fetch_candidates_keeps_only_weth_pairs(monkeypatch):
    weth = "0x4200000000000000000000000000000000000006"
    monkeypatch.setattr(w3_audit, "NETWORKS", {"Base": {"weth": weth}})

    def pool(addr, base, quote, name):
        return {
            "attributes": {
                "address": addr,
                "name": name,
                "reserve_in_usd": "500000",
                "price_change_percentage": {"h1": "5"},
            },
            "relationships": {
                "base_token": {"data": {"id": f"base_{base}"}},
                "quote_token": {"data": {"id": f"base_{quote}"}},
            },
        }

    monkeypatch.setattr(
        w3_audit,
        "fetch",
        AsyncMock(
            return_value={
                "data": [
                    pool("0xp1", "0xaaa", weth, "AAA / WETH"),
                    pool("0xp2", "0xbbb", "0xusdc", "BBB / USDC"),
                ]
            }
        ),
    )

    out, err = await w3_audit.fetch_candidates()

    assert err is None
    assert [c["symbol"] for c in out] == ["AAA"]
    assert out[0]["pair_addr"] == "0xp1"


@pytest.mark.asyncio
async def test_fetch_candidates_reads_several_pages_and_dedupes(monkeypatch):
    monkeypatch.setattr(w3_audit, "NETWORKS", {"Base": {"weth": WETH}})
    pages = {
        1: {"data": [gecko_pool("0xp1", "0xaaa", WETH, "AAA / WETH")]},
        2: {
            "data": [
                gecko_pool("0xp1", "0xaaa", WETH, "AAA / WETH"),
                gecko_pool("0xp2", "0xbbb", WETH, "BBB / WETH"),
            ]
        },
        3: {"data": [gecko_pool("0xp3", "0xccc", WETH, "CCC / WETH")]},
    }
    fetch = AsyncMock(side_effect=lambda url: pages[int(url.rsplit("=", 1)[1])])
    monkeypatch.setattr(w3_audit, "fetch", fetch)

    out, err = await w3_audit.fetch_candidates(pages=3)

    assert err is None
    assert fetch.await_count == 3
    assert [c["symbol"] for c in out] == ["AAA", "BBB", "CCC"]


@pytest.mark.asyncio
async def test_fetch_candidates_keeps_earlier_pages_if_a_later_one_fails(monkeypatch):
    monkeypatch.setattr(w3_audit, "NETWORKS", {"Base": {"weth": WETH}})
    monkeypatch.setattr(
        w3_audit,
        "fetch",
        AsyncMock(
            side_effect=[
                {"data": [gecko_pool("0xp1", "0xaaa", WETH, "AAA / WETH")]},
                {"error": True, "status": 429},
            ]
        ),
    )

    out, err = await w3_audit.fetch_candidates(pages=3)

    assert err is None
    assert [c["symbol"] for c in out] == ["AAA"]


@pytest.mark.asyncio
async def test_fetch_candidates_reports_first_page_failure(monkeypatch):
    monkeypatch.setattr(w3_audit, "NETWORKS", {"Base": {"weth": WETH}})
    monkeypatch.setattr(
        w3_audit, "fetch", AsyncMock(return_value={"error": True, "status": 500})
    )

    assert await w3_audit.fetch_candidates() == ([], "fetch failed (status 500)")


@pytest.mark.asyncio
async def test_fetch_candidates_stops_at_an_empty_page(monkeypatch):
    monkeypatch.setattr(w3_audit, "NETWORKS", {"Base": {"weth": WETH}})
    fetch = AsyncMock(
        side_effect=[
            {"data": [gecko_pool("0xp1", "0xaaa", WETH, "AAA / WETH")]},
            {"data": []},
        ]
    )
    monkeypatch.setattr(w3_audit, "fetch", fetch)

    await w3_audit.fetch_candidates(pages=5)

    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_fetch_candidates_reads_each_dex_and_keeps_the_deeper_pool(monkeypatch):
    monkeypatch.setattr(
        w3_audit,
        "NETWORKS",
        {"Base": {"weth": WETH, "gecko_dexes": ("uniswap-v2-base", "uniswap-v3-base")}},
    )

    def page(url):
        if not url.endswith("=1"):
            return {"data": []}
        if "uniswap-v2-base" in url:
            a, b = (
                gecko_pool("0xv2a", "0xaaa", WETH, "AAA / WETH"),
                gecko_pool("0xv2b", "0xbbb", WETH, "BBB / WETH"),
            )
            a["attributes"]["reserve_in_usd"] = "100"
            b["attributes"]["reserve_in_usd"] = "900"
        else:
            a, b = (
                gecko_pool("0xv3a", "0xaaa", WETH, "AAA / WETH 0.3%"),
                gecko_pool("0xv3b", "0xbbb", WETH, "BBB / WETH 1%"),
            )
            a["attributes"]["reserve_in_usd"] = "500"
            b["attributes"]["reserve_in_usd"] = "50"
        return {"data": [a, b]}

    monkeypatch.setattr(w3_audit, "fetch", AsyncMock(side_effect=page))

    out, err = await w3_audit.fetch_candidates()

    assert err is None
    assert [(c["symbol"], c["pair_addr"]) for c in out] == [
        ("AAA", "0xv3a"),
        ("BBB", "0xv2b"),
    ]


@pytest.mark.asyncio
async def test_fetch_candidates_includes_aerodrome_pools(monkeypatch):
    dexes = NETWORKS["Base"]["gecko_dexes"]
    assert "aerodrome-base" in dexes
    monkeypatch.setattr(
        w3_audit, "NETWORKS", {"Base": {"weth": WETH, "gecko_dexes": dexes}}
    )

    def page(url):
        if "aerodrome-base" in url and url.endswith("=1"):
            return {"data": [gecko_pool("0xaero", "0xaaa", WETH, "AAA / WETH")]}
        return {"data": []}

    monkeypatch.setattr(w3_audit, "fetch", AsyncMock(side_effect=page))

    out, err = await w3_audit.fetch_candidates()

    assert err is None
    assert [(c["symbol"], c["pair_addr"]) for c in out] == [("AAA", "0xaero")]


@pytest.mark.asyncio
async def test_top_holder_ignores_pools_lockers_and_burns(monkeypatch):
    token = "0x" + "ab" * 20
    holders = [
        {"address": "0x" + "01" * 20, "percent": "0.60", "is_contract": 1},
        {"address": "0x" + "02" * 20, "percent": "0.20", "is_locked": 1},
        {"address": "0x000000000000000000000000000000000000dead", "percent": "0.10"},
        {"address": "0x" + "03" * 20, "percent": "0.05", "tag": "Exchange"},
        {"address": "0x" + "04" * 20, "percent": "0.03"},
    ]
    body = {
        "result": {
            token: {"is_open_source": "1", "owner_address": "", "holders": holders}
        }
    }

    class Client:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, url):
            return type("R", (), {"json": lambda self: body})()

    monkeypatch.setattr(w3_audit.httpx, "AsyncClient", Client)

    _, _, gp = await w3_audit.get_security_audit(token, 8453)

    assert gp["top_holder_pct"] == pytest.approx(3.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flags,status,trap",
    [
        ({}, "clear", False),
        ({"is_mintable": "1"}, "owner-controls", False),
        ({"is_proxy": "1", "transfer_pausable": "1"}, "owner-controls", False),
        ({"is_honeypot": "1"}, "honey-pot", True),
        ({"is_mintable": "1", "owner_change_balance": "1"}, "honey-pot", True),
    ],
    ids=["clean", "mintable", "upgradeable", "honeypot", "balance-control"],
)
async def test_owner_controls_are_kept_apart_from_real_traps(
    monkeypatch, flags, status, trap
):
    token = "0x" + "ab" * 20
    body = {
        "result": {
            token: {
                "is_open_source": "1",
                "owner_address": "",
                "buy_tax": "0",
                "sell_tax": "0",
                **flags,
            }
        }
    }

    class Client:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, url):
            return type("R", (), {"json": lambda self: body})()

    monkeypatch.setattr(w3_audit.httpx, "AsyncClient", Client)

    sec_status, _, gp = await w3_audit.get_security_audit(token, 8453)

    assert sec_status == status
    assert gp["is_honeypot"] is trap

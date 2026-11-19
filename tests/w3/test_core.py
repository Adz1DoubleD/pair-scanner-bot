import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from eth_abi import encode
from eth_utils import keccak, to_hex
from web3 import Account, AsyncHTTPProvider, AsyncWeb3, Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

import pair_scanner.w3 as w3_core
from conftest import (
    AERO_FACTORY,
    SAMPLE_ADDR,
    TEST_KEY,
    USDC_BASE,
    base_urls,
    contract_at,
    try_urls,
)
from pair_scanner.networks import (
    AERO_POOL_ABI,
    CHAINLINK_ABI,
    CHAINLINK_ETH_USD,
    ERC20_ABI,
    NETWORKS,
    ROUTER_ABI,
    WETH_ABI,
)

V2_FACTORY = NETWORKS["Base"]["v2_factory"]


def test_checksum_address():
    assert Web3.to_checksum_address(SAMPLE_ADDR.lower()) == SAMPLE_ADDR


def test_wei_conversions():
    assert Web3.to_wei(1, "ether") == 10**18
    assert int(Web3.from_wei(10**18, "ether")) == 1


def test_account_from_key_has_address():
    acct = Account.from_key(TEST_KEY)
    assert isinstance(acct.address, str)
    assert acct.address.startswith("0x")
    assert len(acct.address) == 42


def test_async_web3_constructs():
    w3 = AsyncWeb3(
        AsyncHTTPProvider("https://example.invalid", request_kwargs={"timeout": 10})
    )
    assert hasattr(w3.eth, "contract")
    assert hasattr(w3.eth, "get_logs")
    assert hasattr(w3.eth, "send_raw_transaction")
    assert hasattr(w3.eth, "get_balance")
    assert hasattr(type(w3.eth), "gas_price")
    assert hasattr(w3.eth, "get_transaction_count")
    assert hasattr(w3.eth, "wait_for_transaction_receipt")


@pytest.mark.parametrize(
    "fn", ["balanceOf", "totalSupply", "decimals", "symbol", "approve", "allowance"]
)
def test_erc20_functions_resolve(fn):
    assert hasattr(contract_at(ERC20_ABI).functions, fn)


@pytest.mark.parametrize(
    "fn",
    [
        "getAmountsOut",
        "swapExactETHForTokensSupportingFeeOnTransferTokens",
        "swapExactTokensForETHSupportingFeeOnTransferTokens",
    ],
)
def test_router_functions_resolve(fn):
    assert hasattr(contract_at(ROUTER_ABI).functions, fn)


def test_weth_balanceof_resolves():
    assert hasattr(contract_at(WETH_ABI).functions, "balanceOf")


def test_chainlink_latestrounddata_resolves():
    w3 = AsyncWeb3(AsyncHTTPProvider("https://example.invalid"))
    c = w3.eth.contract(
        address=Web3.to_checksum_address(CHAINLINK_ETH_USD), abi=CHAINLINK_ABI
    )
    assert hasattr(c.functions, "latestRoundData")


def test_sign_transaction_exposes_raw_transaction():
    tx = {
        "nonce": 0,
        "gas": 21000,
        "gasPrice": 10**9,
        "to": SAMPLE_ADDR,
        "value": 0,
        "chainId": 1,
    }
    signed = Account.sign_transaction(tx, TEST_KEY)
    assert hasattr(signed, "raw_transaction")
    assert isinstance(bytes(signed.raw_transaction), bytes)


def test_eth_abi_eth_utils_surface():

    enc = encode(["address", "uint256"], [SAMPLE_ADDR, 0])
    assert isinstance(enc, bytes)
    h = keccak(enc)
    assert isinstance(h, bytes) and len(h) == 32
    assert to_hex(h).startswith("0x")


def test_aero_addresses_are_checksummed():
    conf = NETWORKS["Base"]
    for key in ("aero_router", "aero_factory"):
        assert Web3.to_checksum_address(conf[key]) == conf[key]


@pytest.mark.parametrize("fn", ["factory", "stable"])
def test_aero_pool_functions_resolve(fn):
    assert hasattr(contract_at(AERO_POOL_ABI).functions, fn)


def test_live_contract_reads():
    conf, urls = base_urls()
    weth_addr = Web3.to_checksum_address(conf["weth"])
    usdc = Web3.to_checksum_address(USDC_BASE)

    async def reads(w3):
        token = w3.eth.contract(address=usdc, abi=ERC20_ABI)
        weth = w3.eth.contract(address=weth_addr, abi=WETH_ABI)
        return await asyncio.gather(
            token.functions.decimals().call(),
            token.functions.symbol().call(),
            token.functions.totalSupply().call(),
            token.functions.balanceOf(usdc).call(),
            weth.functions.balanceOf(usdc).call(),
        )

    try:
        decimals, symbol, supply, tok_bal, weth_bal = asyncio.run(try_urls(urls, reads))
    except Exception as e:
        pytest.skip(f"no live RPC answered contract reads ({e})")

    assert decimals == 6
    assert symbol == "USDC"
    assert isinstance(supply, int) and supply > 0
    assert isinstance(tok_bal, int) and tok_bal >= 0
    assert isinstance(weth_bal, int) and weth_bal >= 0


def test_live_getamountsout():
    conf, urls = base_urls()
    path = [Web3.to_checksum_address(conf["weth"]), Web3.to_checksum_address(USDC_BASE)]

    async def call(w3):
        router = w3.eth.contract(
            address=Web3.to_checksum_address(conf["router"]), abi=ROUTER_ABI
        )
        return await router.functions.getAmountsOut(10**15, path).call()

    try:
        amounts = asyncio.run(try_urls(urls, call))
    except Exception as e:
        pytest.skip(f"no live RPC answered getAmountsOut ({e})")

    assert len(amounts) >= 2
    assert amounts[0] == 10**15
    assert amounts[-1] > 0


def test_live_get_balance():
    conf, urls = base_urls()
    addr = Web3.to_checksum_address(conf["weth"])

    async def call(w3):
        return await w3.eth.get_balance(addr)

    try:
        bal = asyncio.run(try_urls(urls, call))
    except Exception as e:
        pytest.skip(f"no live RPC answered get_balance ({e})")

    assert isinstance(bal, int) and bal >= 0


def fake_pool(call):
    fee = SimpleNamespace(call=call)
    functions = SimpleNamespace(fee=lambda: fee)
    return SimpleNamespace(
        eth=SimpleNamespace(
            contract=lambda address, abi: SimpleNamespace(functions=functions)
        )
    )


@pytest.mark.asyncio
async def test_pool_fee_reads_v3_tier_and_caches(monkeypatch):
    monkeypatch.setattr(w3_core, "pool_fees", {})
    call = AsyncMock(return_value=3000)
    w3 = fake_pool(call)

    assert await w3_core._pool_fee(w3, SAMPLE_ADDR) == 3000
    assert await w3_core._pool_fee(w3, SAMPLE_ADDR.lower()) == 3000
    call.assert_awaited_once()


@pytest.mark.asyncio
async def test_pool_fee_is_none_for_v2_pairs(monkeypatch):
    monkeypatch.setattr(w3_core, "pool_fees", {})
    w3 = fake_pool(AsyncMock(side_effect=ContractLogicError("execution reverted")))

    assert await w3_core._pool_fee(w3, SAMPLE_ADDR) is None
    assert w3_core.pool_fees == {SAMPLE_ADDR.lower(): None}


@pytest.mark.asyncio
async def test_pool_fee_does_not_cache_network_errors(monkeypatch):
    monkeypatch.setattr(w3_core, "pool_fees", {})
    w3 = fake_pool(AsyncMock(side_effect=TimeoutError("rpc down")))

    with pytest.raises(TimeoutError):
        await w3_core._pool_fee(w3, SAMPLE_ADDR)
    assert w3_core.pool_fees == {}


def fake_aero_pool(factory_call, stable_call=None):
    stable_call = stable_call or AsyncMock(return_value=False)
    functions = SimpleNamespace(
        factory=lambda: SimpleNamespace(call=factory_call),
        stable=lambda: SimpleNamespace(call=stable_call),
    )
    return SimpleNamespace(
        eth=SimpleNamespace(
            contract=lambda address, abi: SimpleNamespace(functions=functions)
        )
    )


AERO_CONF = {"aero_factory": AERO_FACTORY}


@pytest.mark.asyncio
@pytest.mark.parametrize("stable", [False, True])
async def test_aero_stable_reads_flag_for_aerodrome_pools_and_caches(
    monkeypatch, stable
):
    monkeypatch.setattr(w3_core, "aero_pools", {})
    factory = AsyncMock(return_value=AERO_FACTORY.lower())
    w3 = fake_aero_pool(factory, AsyncMock(return_value=stable))

    assert await w3_core._aero_stable(w3, AERO_CONF, SAMPLE_ADDR) is stable
    assert await w3_core._aero_stable(w3, AERO_CONF, SAMPLE_ADDR.lower()) is stable
    factory.assert_awaited_once()


@pytest.mark.asyncio
async def test_aero_stable_is_none_for_uniswap_pools(monkeypatch):
    monkeypatch.setattr(w3_core, "aero_pools", {})
    stable = AsyncMock()
    w3 = fake_aero_pool(AsyncMock(return_value=V2_FACTORY), stable)

    assert await w3_core._aero_stable(w3, AERO_CONF, SAMPLE_ADDR) is None
    stable.assert_not_awaited()
    assert w3_core.aero_pools == {SAMPLE_ADDR.lower(): None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [ContractLogicError("execution reverted"), BadFunctionCallOutput("no data")],
)
async def test_aero_stable_is_none_when_factory_call_fails(monkeypatch, error):
    monkeypatch.setattr(w3_core, "aero_pools", {})
    w3 = fake_aero_pool(AsyncMock(side_effect=error))

    assert await w3_core._aero_stable(w3, AERO_CONF, SAMPLE_ADDR) is None
    assert w3_core.aero_pools == {SAMPLE_ADDR.lower(): None}


@pytest.mark.asyncio
async def test_aero_stable_does_not_cache_network_errors(monkeypatch):
    monkeypatch.setattr(w3_core, "aero_pools", {})
    w3 = fake_aero_pool(AsyncMock(side_effect=TimeoutError("rpc down")))

    with pytest.raises(TimeoutError):
        await w3_core._aero_stable(w3, AERO_CONF, SAMPLE_ADDR)
    assert w3_core.aero_pools == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "conf, pair", [({}, SAMPLE_ADDR), (AERO_CONF, None)], ids=["no-factory", "no-pair"]
)
async def test_aero_stable_skips_the_rpc_when_it_cannot_apply(conf, pair):
    w3 = SimpleNamespace(eth=SimpleNamespace(contract=MagicMock()))

    assert await w3_core._aero_stable(w3, conf, pair) is None
    w3.eth.contract.assert_not_called()

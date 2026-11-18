import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import (
    AERO_FACTORY,
    TEST_KEY,
    USDC_BASE,
    base_urls,
    contract_at,
    try_urls,
)
from eth_abi import decode
from hexbytes import HexBytes
from web3 import Account, AsyncHTTPProvider, AsyncWeb3, Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

import pair_scanner.w3.trade as w3_trade
from pair_scanner.networks import (
    AERO_ROUTER_ABI,
    ERC20_ABI,
    MAX_UINT256,
    NETWORKS,
    ROUTER_ABI,
)
from pair_scanner.w3.trade import NonceManager

AERO_ROUTES_TYPE = "(address,address,bool,address)[]"


AERO_SWAP_FNS = [
    "getAmountsOut",
    "swapExactETHForTokensSupportingFeeOnTransferTokens",
    "swapExactTokensForETHSupportingFeeOnTransferTokens",
]


def _aero_router(w3, conf):
    return w3.eth.contract(
        address=Web3.to_checksum_address(conf["aero_router"]), abi=AERO_ROUTER_ABI
    )


def _lower_routes(routes):
    return [(a.lower(), b.lower(), s, f.lower()) for a, b, s, f in routes]


def test_nonce_manager_reserves_increments_resets():
    async def run():
        nm = NonceManager()
        w3 = MagicMock()
        w3.eth.get_transaction_count = AsyncMock(return_value=5)
        addr = "0x" + "11" * 20
        assert await nm.reserve(w3, addr, 8453) == 5
        assert await nm.reserve(w3, addr, 8453) == 6
        await nm.reset(addr)
        assert await nm.reserve(w3, addr, 8453) == 5

    asyncio.run(run())


def test_build_sign_buy_tx():
    conf = NETWORKS["Base"]
    w3 = AsyncWeb3(AsyncHTTPProvider("https://example.invalid"))
    router = w3.eth.contract(
        address=Web3.to_checksum_address(conf["router"]), abi=ROUTER_ABI
    )
    acct = Account.from_key(TEST_KEY)
    path = [Web3.to_checksum_address(conf["weth"]), Web3.to_checksum_address(USDC_BASE)]

    async def build():
        return (
            await router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                0, path, acct.address, 9999999999
            ).build_transaction(
                {
                    "from": acct.address,
                    "value": 10**15,
                    "nonce": 0,
                    "gasPrice": 10**9,
                    "gas": 300000,
                    "chainId": conf["chain_id"],
                }
            )
        )

    tx = asyncio.run(build())
    assert tx["to"] == Web3.to_checksum_address(conf["router"])
    assert tx["value"] == 10**15
    assert tx["chainId"] == conf["chain_id"]
    assert str(tx["data"]).startswith("0x")
    signed = Account.sign_transaction(tx, TEST_KEY)
    assert isinstance(bytes(signed.raw_transaction), bytes)


def test_build_sign_sell_tx():
    conf = NETWORKS["Base"]
    w3 = AsyncWeb3(AsyncHTTPProvider("https://example.invalid"))
    router = w3.eth.contract(
        address=Web3.to_checksum_address(conf["router"]), abi=ROUTER_ABI
    )
    acct = Account.from_key(TEST_KEY)
    path = [Web3.to_checksum_address(USDC_BASE), Web3.to_checksum_address(conf["weth"])]

    async def build():
        return (
            await router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                10**6, 0, path, acct.address, 9999999999
            ).build_transaction(
                {
                    "from": acct.address,
                    "nonce": 0,
                    "gasPrice": 10**9,
                    "gas": 300000,
                    "chainId": conf["chain_id"],
                }
            )
        )

    tx = asyncio.run(build())
    assert tx["to"] == Web3.to_checksum_address(conf["router"])
    assert str(tx["data"]).startswith("0x")
    signed = Account.sign_transaction(tx, TEST_KEY)
    assert isinstance(bytes(signed.raw_transaction), bytes)


def test_build_sign_approve_tx():
    conf = NETWORKS["Base"]
    w3 = AsyncWeb3(AsyncHTTPProvider("https://example.invalid"))
    token = w3.eth.contract(address=Web3.to_checksum_address(USDC_BASE), abi=ERC20_ABI)
    acct = Account.from_key(TEST_KEY)
    router_addr = Web3.to_checksum_address(conf["router"])

    async def build():
        return await token.functions.approve(
            router_addr, MAX_UINT256
        ).build_transaction(
            {
                "from": acct.address,
                "nonce": 0,
                "gasPrice": 10**9,
                "gas": 100000,
                "chainId": conf["chain_id"],
            }
        )

    tx = asyncio.run(build())
    assert tx["to"] == Web3.to_checksum_address(USDC_BASE)
    assert str(tx["data"]).startswith("0x")
    signed = Account.sign_transaction(tx, TEST_KEY)
    assert isinstance(bytes(signed.raw_transaction), bytes)


@pytest.mark.parametrize("fn", AERO_SWAP_FNS)
def test_aero_router_functions_resolve(fn):
    assert hasattr(contract_at(AERO_ROUTER_ABI).functions, fn)


def test_aero_route_uses_the_aerodrome_factory():
    conf = NETWORKS["Base"]
    assert w3_trade.aero_route(conf, "0xin", "0xout", True) == (
        "0xin",
        "0xout",
        True,
        AERO_FACTORY,
    )


def test_build_sign_aero_buy_tx():
    conf = NETWORKS["Base"]
    w3 = AsyncWeb3(AsyncHTTPProvider("https://example.invalid"))
    router = _aero_router(w3, conf)
    acct = Account.from_key(TEST_KEY)
    weth = Web3.to_checksum_address(conf["weth"])
    usdc = Web3.to_checksum_address(USDC_BASE)
    routes = [w3_trade.aero_route(conf, weth, usdc, False)]

    async def build():
        return (
            await router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                0, routes, acct.address, 9999999999
            ).build_transaction(
                {
                    "from": acct.address,
                    "value": 10**15,
                    "nonce": 0,
                    "gasPrice": 10**9,
                    "gas": 300000,
                    "chainId": conf["chain_id"],
                }
            )
        )

    tx = asyncio.run(build())
    assert tx["to"] == Web3.to_checksum_address(conf["aero_router"])
    assert tx["value"] == 10**15
    _, decoded, to, _ = decode(
        ["uint256", AERO_ROUTES_TYPE, "address", "uint256"],
        bytes(HexBytes(tx["data"]))[4:],
    )
    assert _lower_routes(decoded) == [
        (weth.lower(), usdc.lower(), False, AERO_FACTORY.lower())
    ]
    assert to.lower() == acct.address.lower()
    signed = Account.sign_transaction(tx, TEST_KEY)
    assert isinstance(bytes(signed.raw_transaction), bytes)


def test_build_sign_aero_sell_tx():
    conf = NETWORKS["Base"]
    w3 = AsyncWeb3(AsyncHTTPProvider("https://example.invalid"))
    router = _aero_router(w3, conf)
    acct = Account.from_key(TEST_KEY)
    weth = Web3.to_checksum_address(conf["weth"])
    usdc = Web3.to_checksum_address(USDC_BASE)
    routes = [w3_trade.aero_route(conf, usdc, weth, True)]

    async def build():
        return (
            await router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                10**6, 0, routes, acct.address, 9999999999
            ).build_transaction(
                {
                    "from": acct.address,
                    "nonce": 0,
                    "gasPrice": 10**9,
                    "gas": 300000,
                    "chainId": conf["chain_id"],
                }
            )
        )

    tx = asyncio.run(build())
    assert tx["to"] == Web3.to_checksum_address(conf["aero_router"])
    amount_in, _, decoded, _, _ = decode(
        ["uint256", "uint256", AERO_ROUTES_TYPE, "address", "uint256"],
        bytes(HexBytes(tx["data"]))[4:],
    )
    assert amount_in == 10**6
    assert _lower_routes(decoded) == [
        (usdc.lower(), weth.lower(), True, AERO_FACTORY.lower())
    ]
    signed = Account.sign_transaction(tx, TEST_KEY)
    assert isinstance(bytes(signed.raw_transaction), bytes)


def test_live_aero_getamountsout():
    conf, urls = base_urls()
    weth = Web3.to_checksum_address(conf["weth"])
    usdc = Web3.to_checksum_address(USDC_BASE)

    async def call(w3):
        router = _aero_router(w3, conf)
        routes = [w3_trade.aero_route(conf, weth, usdc, False)]
        return await router.functions.getAmountsOut(10**15, routes).call()

    try:
        amounts = asyncio.run(try_urls(urls, call))
    except (ContractLogicError, BadFunctionCallOutput) as e:
        pytest.fail(f"Aerodrome router rejected getAmountsOut ({e})")
    except Exception as e:
        pytest.skip(f"no live RPC answered Aerodrome getAmountsOut ({e})")

    assert len(amounts) == 2
    assert amounts[0] == 10**15
    assert amounts[-1] > 0

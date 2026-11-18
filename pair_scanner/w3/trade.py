import asyncio
import time

import sentry_sdk
from eth_abi import encode
from eth_utils import keccak, to_hex
from web3 import Web3
from web3.exceptions import TimeExhausted

from ..networks import (
    AERO_ROUTER_ABI,
    ERC20_ABI,
    MAX_UINT256,
    ROUTER_ABI,
    V3_QUOTER_ABI,
    V3_ROUTER_ABI,
    V3_ROUTER_SELF,
)
from ..settings import OPTIONS, SETTINGS
from . import open_w3, pool_kind

DUST_GAS_UNITS = 300_000
WATCH_ONLY = "Trading is off (watch-only)"
RECEIPT_TIMEOUT = 60


class NonceManager:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._next = {}

    async def reserve(self, w3, address, chain_id):
        async with self._lock:
            key = (chain_id, address)
            latest = await w3.eth.get_transaction_count(address, "pending")
            nonce = max(latest, self._next.get(key, 0))
            self._next[key] = nonce + 1
            return nonce

    async def reset(self, address):
        async with self._lock:
            for key in [k for k in self._next if k[1] == address]:
                del self._next[key]


nonce_manager = NonceManager()


async def execute_buy(conf: dict, token_addr, amount_eth, pair_addr=None):
    if not int(OPTIONS.get("trading_enabled", 0)) or not SETTINGS.private_key:
        return None, WATCH_ONLY
    private_key = SETTINGS.private_key
    buffer = float(OPTIONS.get("slippage", 5.0))
    w3 = await open_w3(conf)

    account = w3.eth.account.from_key(private_key)
    weth = Web3.to_checksum_address(conf["weth"])
    token = Web3.to_checksum_address(token_addr)
    amount_wei = w3.to_wei(amount_eth, "ether")

    try:
        stable, fee = await pool_kind(w3, conf, pair_addr)
        if stable is not None:
            router = w3.eth.contract(
                address=Web3.to_checksum_address(conf["aero_router"]),
                abi=AERO_ROUTER_ABI,
            )
            routes = [aero_route(conf, weth, token, stable)]
            amounts_out = await router.functions.getAmountsOut(
                amount_wei, routes
            ).call()
            min_output = int(amounts_out[-1] * (1 - (buffer / 100)))
            swap = router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                min_output, routes, account.address, int(time.time()) + 120
            )
        elif fee is None:
            router = w3.eth.contract(
                address=Web3.to_checksum_address(conf["router"]), abi=ROUTER_ABI
            )
            path = [weth, token]
            amounts_out = await router.functions.getAmountsOut(amount_wei, path).call()
            min_output = int(amounts_out[-1] * (1 - (buffer / 100)))
            swap = router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                min_output, path, account.address, int(time.time()) + 120
            )
        else:
            quoter = w3.eth.contract(
                address=Web3.to_checksum_address(conf["v3_quoter"]), abi=V3_QUOTER_ABI
            )
            quote = await quoter.functions.quoteExactInputSingle(
                (weth, token, amount_wei, fee, 0)
            ).call()
            min_output = int(quote[0] * (1 - (buffer / 100)))
            router = w3.eth.contract(
                address=Web3.to_checksum_address(conf["v3_router"]), abi=V3_ROUTER_ABI
            )
            swap = router.functions.exactInputSingle(
                (weth, token, fee, account.address, amount_wei, min_output, 0)
            )

        gas_price = await w3.eth.gas_price
        gas_mult = conf.get("gas_multiplier", 1.25)
        gas_limit_token = conf.get("gas_limit_token", 300000)

        base_tx = {
            "from": account.address,
            "value": amount_wei,
            "nonce": await nonce_manager.reserve(w3, account.address, conf["chain_id"]),
            "gasPrice": gas_price,
            "chainId": conf["chain_id"],
        }

        estimated_gas = await swap.estimate_gas(base_tx)
        tx = await swap.build_transaction(
            {
                **base_tx,
                "gas": min(int(estimated_gas * gas_mult), gas_limit_token),
            }
        )

        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)

        try:
            receipt = await w3.eth.wait_for_transaction_receipt(
                tx_hash, timeout=RECEIPT_TIMEOUT
            )
        except TimeExhausted as e:
            sentry_sdk.capture_exception(e)
            return tx_hash.hex(), "PENDING"
        if receipt["status"] != 1:
            return None, "BUY_REVERTED"
        return tx_hash.hex(), None

    except Exception as e:
        await nonce_manager.reset(account.address)
        sentry_sdk.capture_exception(e)
        return None, str(e)


async def execute_sell(conf: dict, pos, skip_dust=False):
    private_key = SETTINGS.private_key

    total_slippage = float(pos.get("sell_tax") or 0) + float(
        OPTIONS.get("slippage", 2.0)
    )

    w3 = await open_w3(conf)

    account = w3.eth.account.from_key(private_key)
    token_addr = Web3.to_checksum_address(pos["target_address"])
    weth_addr = Web3.to_checksum_address(conf["weth"])
    token_contract = w3.eth.contract(address=token_addr, abi=ERC20_ABI)

    balance = await token_contract.functions.balanceOf(account.address).call()
    if balance == 0:
        return None, "No token balance"

    try:
        gas_price = await w3.eth.gas_price
        gas_mult = conf.get("gas_multiplier", 1.25)
        gas_limit_token = conf.get("gas_limit_token", 300000)
        deadline = int(time.time()) + 120

        pair_addr = pos.get("pair_address")
        stable, fee = await pool_kind(w3, conf, pair_addr)
        if stable is not None:
            router_addr = Web3.to_checksum_address(conf["aero_router"])
            router = w3.eth.contract(address=router_addr, abi=AERO_ROUTER_ABI)
            routes = [aero_route(conf, token_addr, weth_addr, stable)]
            eth_out = (await router.functions.getAmountsOut(balance, routes).call())[-1]
        elif fee is None:
            router_addr = Web3.to_checksum_address(conf["router"])
            router = w3.eth.contract(address=router_addr, abi=ROUTER_ABI)
            path = [token_addr, weth_addr]
            eth_out = (await router.functions.getAmountsOut(balance, path).call())[-1]
        else:
            router_addr = Web3.to_checksum_address(conf["v3_router"])
            router = w3.eth.contract(address=router_addr, abi=V3_ROUTER_ABI)
            quoter = w3.eth.contract(
                address=Web3.to_checksum_address(conf["v3_quoter"]), abi=V3_QUOTER_ABI
            )
            eth_out = (
                await quoter.functions.quoteExactInputSingle(
                    (token_addr, weth_addr, balance, fee, 0)
                ).call()
            )[0]

        if skip_dust and eth_out < gas_price * DUST_GAS_UNITS:
            return None, "dust"
        min_eth = int(eth_out * (1 - (total_slippage / 100)))

        if stable is not None:
            swap = router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                balance, min_eth, routes, account.address, deadline
            )
        elif fee is None:
            swap = router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                balance, min_eth, path, account.address, deadline
            )
        else:
            swap = router.functions.multicall(
                deadline,
                [
                    router.encode_abi(
                        "exactInputSingle",
                        args=[
                            (
                                token_addr,
                                weth_addr,
                                fee,
                                V3_ROUTER_SELF,
                                balance,
                                min_eth,
                                0,
                            )
                        ],
                    ),
                    router.encode_abi("unwrapWETH9", args=[min_eth, account.address]),
                ],
            )

        allowance = await token_contract.functions.allowance(
            account.address, router_addr
        ).call()
        approved_now = False
        if allowance < balance:
            approve_base = {
                "from": account.address,
                "nonce": await nonce_manager.reserve(
                    w3, account.address, conf["chain_id"]
                ),
                "gasPrice": gas_price,
            }
            est_app_gas = await token_contract.functions.approve(
                router_addr, MAX_UINT256
            ).estimate_gas(approve_base)

            approve_tx = await token_contract.functions.approve(
                router_addr, MAX_UINT256
            ).build_transaction(
                {
                    **approve_base,
                    "gas": min(int(est_app_gas * gas_mult), gas_limit_token),
                }
            )

            signed_app = w3.eth.account.sign_transaction(approve_tx, private_key)
            app_hash = await w3.eth.send_raw_transaction(signed_app.raw_transaction)
            app_receipt = await w3.eth.wait_for_transaction_receipt(
                app_hash, timeout=30
            )
            if app_receipt["status"] != 1:
                return None, "Approve failed"
            approved_now = True

        sell_base = {
            "from": account.address,
            "nonce": await nonce_manager.reserve(w3, account.address, conf["chain_id"]),
            "gasPrice": gas_price,
            "chainId": conf["chain_id"],
        }

        for attempt in range(3):
            try:
                estimated_sell_gas = await swap.estimate_gas(sell_base)
                break
            except Exception as est_e:
                if (
                    approved_now
                    and any(
                        msg in str(est_e) for msg in ("TRANSFER_FROM_FAILED", "STF")
                    )
                    and attempt < 2
                ):
                    await asyncio.sleep(2)
                    continue
                await nonce_manager.reset(account.address)
                return None, est_e.args[0] if est_e.args else str(est_e)

        tx = await swap.build_transaction(
            {
                **sell_base,
                "gas": min(int(estimated_sell_gas * gas_mult), gas_limit_token),
            }
        )

        signed_sell = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = await w3.eth.send_raw_transaction(signed_sell.raw_transaction)

        try:
            receipt = await w3.eth.wait_for_transaction_receipt(
                tx_hash, timeout=RECEIPT_TIMEOUT
            )
        except TimeExhausted as e:
            sentry_sdk.capture_exception(e)
            return None, "PENDING"

        if receipt["status"] == 1:
            return tx_hash.hex(), None

        try:
            await w3.eth.call(
                {
                    "from": tx["from"],
                    "to": tx["to"],
                    "data": tx["data"],
                    "value": tx.get("value", 0),
                },
                receipt["blockNumber"],
            )
        except Exception as call_e:
            return None, str(call_e)

        return None, "SELL_REVERTED"

    except Exception as e:
        await nonce_manager.reset(account.address)
        sentry_sdk.capture_exception(e)
        return None, str(e)


async def simulate_sell(conf, token_addr, pair_addr=None):
    try:
        w3 = await open_w3(conf)
        weth_addr = Web3.to_checksum_address(conf["weth"])
        token_addr = Web3.to_checksum_address(token_addr)
        wallet = Web3.to_checksum_address(SETTINGS.wallet_address)

        stable, fee = await pool_kind(w3, conf, pair_addr)
        if stable is not None:
            router_addr = Web3.to_checksum_address(conf["aero_router"])
        else:
            router_addr = Web3.to_checksum_address(
                conf["router"] if fee is None else conf["v3_router"]
            )

        token = w3.eth.contract(address=token_addr, abi=ERC20_ABI)

        decimals = await token.functions.decimals().call()
        amount_in = 10**decimals
        big_int = 10 ** (decimals + 9)
        big_hex = to_hex(big_int.to_bytes(32, "big"))

        bal_key = None
        for slot in range(30):
            key = to_hex(keccak(encode(["address", "uint256"], [wallet, slot])))
            ov = {token_addr: {"stateDiff": {key: big_hex}}}
            try:
                if (
                    await token.functions.balanceOf(wallet).call(state_override=ov)
                    == big_int
                ):
                    bal_key = key
                    break
            except Exception:
                continue

        allow_key = None
        for slot in range(30):
            inner = keccak(encode(["address", "uint256"], [wallet, slot]))
            key = to_hex(keccak(encode(["address"], [router_addr]) + inner))
            ov = {token_addr: {"stateDiff": {key: big_hex}}}
            try:
                if (
                    await token.functions.allowance(wallet, router_addr).call(
                        state_override=ov
                    )
                    == big_int
                ):
                    allow_key = key
                    break
            except Exception:
                continue

        if bal_key is None or allow_key is None:
            return None

        state_override = {
            token_addr: {"stateDiff": {bal_key: big_hex, allow_key: big_hex}}
        }

        if stable is not None:
            router = w3.eth.contract(address=router_addr, abi=AERO_ROUTER_ABI)
            swap = router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                amount_in,
                0,
                [aero_route(conf, token_addr, weth_addr, stable)],
                wallet,
                2**256 - 1,
            )
        elif fee is None:
            router = w3.eth.contract(address=router_addr, abi=ROUTER_ABI)
            swap = router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                amount_in, 0, [token_addr, weth_addr], wallet, 2**256 - 1
            )
        else:
            router = w3.eth.contract(address=router_addr, abi=V3_ROUTER_ABI)
            swap = router.functions.exactInputSingle(
                (token_addr, weth_addr, fee, wallet, amount_in, 0, 0)
            )

        try:
            await swap.call({"from": wallet}, "latest", state_override=state_override)
            return True
        except Exception as e:
            err = str(e).lower()
            return False if ("revert" in err or "execution reverted" in err) else None

    except Exception:
        return None


def aero_route(conf, token_in, token_out, stable):
    return (
        token_in,
        token_out,
        stable,
        Web3.to_checksum_address(conf["aero_factory"]),
    )

import asyncio
import time

from aiohttp import ClientSession, TCPConnector
from web3 import AsyncHTTPProvider, AsyncWeb3, Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from ..networks import AERO_POOL_ABI, NETWORKS, V3_POOL_ABI

w3_cache: dict[str, AsyncWeb3] = {}
rpc_failed: dict[str, float] = {}
rpc_working: dict[str, tuple[str, float]] = {}
RPC_RETRY_AFTER = 300

pool_fees: dict[str, int | None] = {}
aero_pools: dict[str, bool | None] = {}


async def get_w3_service(config):
    rpcs = [config["rpc"]] + config.get("rpc_fallbacks", [])

    async def probe(rpc):
        provider = AsyncHTTPProvider(rpc["url"], request_kwargs={"timeout": 5})
        await provider.cache_async_session(
            ClientSession(
                raise_for_status=True, connector=TCPConnector(force_close=True)
            )
        )
        w3 = AsyncWeb3(provider)
        try:
            start = time.perf_counter()
            if await w3.is_connected():
                await w3.eth.block_number
                latency = int((time.perf_counter() - start) * 1000)
                return f"{rpc['name']} 🟢 {latency}ms", True
            return f"{rpc['name']} ❌ Offline", False
        except Exception as e:
            err = str(e).lower()
            if "429" in err or "rate limit" in err:
                reason = "Rate Limited"
            elif "timeout" in err:
                reason = "Timeout"
            elif "connection" in err:
                reason = "Connection Error"
            else:
                reason = "Offline"
            return f"{rpc['name']} ❌ {reason}", False
        finally:
            await provider.disconnect()

    checks = await asyncio.gather(*(probe(r) for r in rpcs))
    lines = [line for line, _ in checks]
    down = [line for line, ok in checks if not ok]
    return lines, down


async def close_w3():
    for w3 in w3_cache.values():
        await w3.provider.disconnect()
    w3_cache.clear()
    rpc_failed.clear()
    rpc_working.clear()


async def open_w3(conf: dict) -> AsyncWeb3:
    network_name = next((k for k, v in NETWORKS.items() if v is conf), "")
    now = time.time()

    if network_name in rpc_working:
        url, ts = rpc_working[network_name]
        if (
            now - rpc_failed.get(url, 0) >= RPC_RETRY_AFTER
            and now - ts < 300
            and url in w3_cache
        ):
            return w3_cache[url]

    rpcs = [conf["rpc"]] + conf.get("rpc_fallbacks", [])

    for rpc in rpcs:
        url = rpc["url"]
        name = rpc["name"]

        if time.time() - rpc_failed.get(url, 0) < RPC_RETRY_AFTER:
            continue

        if url not in w3_cache:
            provider = AsyncHTTPProvider(url, request_kwargs={"timeout": 10})
            await provider.cache_async_session(
                ClientSession(
                    raise_for_status=True, connector=TCPConnector(force_close=True)
                )
            )
            w3_cache[url] = AsyncWeb3(provider)
        try:
            await w3_cache[url].eth.block_number
            rpc_working[network_name] = (url, time.time())
            return w3_cache[url]
        except Exception:
            print(f"⚠️ [RPC] {name} ({network_name}) failed, marking as unavailable")
            rpc_failed[url] = time.time()
            continue

    raise Exception("❌ All RPC endpoints failed")


async def aero_stable(w3, conf, pair_addr):
    factory = conf.get("aero_factory")
    if not factory or not pair_addr:
        return None
    key = pair_addr.lower()
    if key not in aero_pools:
        pool = w3.eth.contract(
            address=Web3.to_checksum_address(pair_addr), abi=AERO_POOL_ABI
        )
        try:
            if (await pool.functions.factory().call()).lower() == factory.lower():
                aero_pools[key] = await pool.functions.stable().call()
            else:
                aero_pools[key] = None
        except (BadFunctionCallOutput, ContractLogicError):
            aero_pools[key] = None
    return aero_pools[key]


async def pool_fee(w3, pair_addr):
    key = pair_addr.lower()
    if key not in pool_fees:
        pool = w3.eth.contract(
            address=Web3.to_checksum_address(pair_addr), abi=V3_POOL_ABI
        )
        try:
            pool_fees[key] = await pool.functions.fee().call()
        except (BadFunctionCallOutput, ContractLogicError):
            pool_fees[key] = None
    return pool_fees[key]


async def pool_kind(w3, conf, pair_addr):
    stable = await aero_stable(w3, conf, pair_addr)
    fee = await pool_fee(w3, pair_addr) if pair_addr and stable is None else None
    return stable, fee

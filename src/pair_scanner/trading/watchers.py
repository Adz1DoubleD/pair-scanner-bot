"""Watching the factories for new pairs and reading their token details."""

import asyncio

import sentry_sdk
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from ..db.pairs import (
    pairs_add,
    pairs_get_by_status,
    pairs_mark_as_invalid,
    pairs_update_metadata,
)
from ..networks import (
    AERO_POOL_CREATED_TOPIC,
    ERC20_ABI,
    NETWORKS,
    PAIR_CREATED_TOPIC,
    POOL_CREATED_TOPIC,
)
from ..w3 import open_w3
from ..w3.audit import parse_pair_created


async def watch_new_pairs() -> None:
    last_block = None
    network_name = "Base"
    conf = NETWORKS[network_name]
    BUFFER = 2
    POLL_SECONDS = 30
    MAX_BLOCK_RANGE = 10
    REPORT_AFTER_FAILURES = 30
    failures = 0
    factories = [
        f
        for f in (
            conf["v2_factory"],
            conf["v3_factory"],
            conf.get("aero_factory"),
        )
        if f
    ]
    topics = [PAIR_CREATED_TOPIC, POOL_CREATED_TOPIC, AERO_POOL_CREATED_TOPIC]

    while True:
        try:
            w3 = await open_w3(conf)
            blockchain_tip = await w3.eth.block_number
            current_block = blockchain_tip - BUFFER

            if last_block is None:
                last_block = current_block - 1

            if current_block > last_block:
                from_block = last_block + 1

                while from_block <= current_block:
                    to_block = min(from_block + MAX_BLOCK_RANGE - 1, current_block)
                    logs = await w3.eth.get_logs(
                        {
                            "fromBlock": from_block,
                            "toBlock": to_block,
                            "address": factories,
                            "topics": [topics],
                        }
                    )

                    if logs:
                        for log in logs:
                            t0, t1, pair = parse_pair_created(log)

                            weth = conf["weth"].lower()
                            if t0.lower() == weth or t1.lower() == weth:
                                target = t1 if t0.lower() == weth else t0
                                await pairs_add(target, pair, network_name)

                    last_block = to_block
                    from_block = to_block + 1

            failures = 0

        except Exception as e:
            failures += 1
            is_timeout = (
                isinstance(e, (asyncio.TimeoutError, TimeoutError))
                or "timeout" in str(e).lower()
            )
            is_rpc_sync = "-32602" in str(e) or "block range" in str(e).lower()
            is_server_error = (
                "status=500" in str(e) or "internal server error" in str(e).lower()
            )
            is_bad_request = "Bad Request" in str(e)

            transient = is_timeout or is_rpc_sync or is_server_error or is_bad_request
            if not transient or failures == REPORT_AFTER_FAILURES:
                sentry_sdk.capture_exception(e)

            await asyncio.sleep(10)
            continue

        await asyncio.sleep(POLL_SECONDS)


async def watch_pair_metadata() -> None:
    failures: dict[str, int] = {}
    while True:
        pending = await pairs_get_by_status(status="pending", table="pairs", limit=5)

        if not pending:
            await asyncio.sleep(30)
            continue

        for row in pending:
            target_addr = row["target_address"]
            conf = NETWORKS[row["network"]]
            w3 = await open_w3(conf)
            try:
                contract = w3.eth.contract(
                    address=Web3.to_checksum_address(target_addr), abi=ERC20_ABI
                )
                symbol = await contract.functions.symbol().call()
                decimals = await contract.functions.decimals().call()
            except (BadFunctionCallOutput, ContractLogicError, ValueError):
                await pairs_mark_as_invalid(target_addr)
                continue
            except Exception:
                failures[target_addr] = failures.get(target_addr, 0) + 1
                if failures[target_addr] >= 5:
                    failures.pop(target_addr)
                    await pairs_mark_as_invalid(target_addr)
                continue

            failures.pop(target_addr, None)
            await pairs_update_metadata(target_addr, symbol, decimals)

        await asyncio.sleep(5)

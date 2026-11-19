"""Chain settings and RPC endpoints for each network."""

from typing import Any

from .settings import SETTINGS


def _rpcs(*entries: dict[str, Any]) -> dict[str, Any]:
    usable = [e for e in entries if e]
    return {"rpc": usable[0], "rpc_fallbacks": usable[1:]}


def _build_networks() -> dict[str, dict[str, Any]]:
    alchemy, ankr = SETTINGS.alchemy_api_key, SETTINGS.ankr_api_key
    return {
        "Ethereum": {
            **_rpcs(
                alchemy
                and {
                    "url": f"https://eth-mainnet.g.alchemy.com/v2/{alchemy}",
                    "name": "Alchemy",
                },
                ankr and {"url": f"https://rpc.ankr.com/eth/{ankr}", "name": "Ankr"},
                {"url": "https://ethereum-rpc.publicnode.com", "name": "PublicNode"},
                {"url": "https://eth.drpc.org", "name": "dRPC"},
            ),
            "chain_id": 1,
            "scan": "https://etherscan.io/tx/",
            "dex": "https://dexscreener.com/ethereum/",
            "router": "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
            "v2_factory": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
            "v3_factory": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
            "weth": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            "gas_multiplier": 1.2,
            "min_sweep_wei": 5000000000000000,
            "gas_limit_eth": 21000,
            "gas_limit_token": 300000,
        },
        "Base": {
            **_rpcs(
                alchemy
                and {
                    "url": f"https://base-mainnet.g.alchemy.com/v2/{alchemy}",
                    "name": "Alchemy",
                },
                ankr and {"url": f"https://rpc.ankr.com/base/{ankr}", "name": "Ankr"},
                {"url": "https://base.publicnode.com", "name": "PublicNode"},
                {"url": "https://base.drpc.org", "name": "dRPC"},
            ),
            "chain_id": 8453,
            "scan": "https://basescan.org/tx/",
            "dex": "https://dexscreener.com/base/",
            "gecko_dexes": ("uniswap-v2-base", "uniswap-v3-base", "aerodrome-base"),
            "router": "0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24",
            "v2_factory": "0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6",
            "v3_factory": "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
            "v3_router": "0x2626664c2603336E57B271c5C0b26F421741e481",
            "v3_quoter": "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a",
            "aero_router": "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43",
            "aero_factory": "0x420DD381b31aEf6683db6B902084cB0FFECe40Da",
            "weth": "0x4200000000000000000000000000000000000006",
            "gas_multiplier": 1.1,
            "min_sweep_wei": 100000000000000,
            "gas_limit_eth": 21000,
            "gas_limit_token": 300000,
        },
    }


NETWORKS = _build_networks()


def refresh_networks() -> None:
    NETWORKS.clear()
    NETWORKS.update(_build_networks())


ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
]

WETH_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    }
]

ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactETHForTokensSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForETHSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
        ],
        "name": "getAmountsOut",
        "outputs": [
            {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

V3_POOL_ABI = [
    {
        "inputs": [],
        "name": "fee",
        "outputs": [{"internalType": "uint24", "name": "", "type": "uint24"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {
                "internalType": "uint16",
                "name": "observationCardinality",
                "type": "uint16",
            },
            {
                "internalType": "uint16",
                "name": "observationCardinalityNext",
                "type": "uint16",
            },
            {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

_V3_EXACT_INPUT_SINGLE = {
    "components": [
        {"internalType": "address", "name": "tokenIn", "type": "address"},
        {"internalType": "address", "name": "tokenOut", "type": "address"},
        {"internalType": "uint24", "name": "fee", "type": "uint24"},
        {"internalType": "address", "name": "recipient", "type": "address"},
        {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
        {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
        {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
    ],
    "internalType": "struct IV3SwapRouter.ExactInputSingleParams",
    "name": "params",
    "type": "tuple",
}

V3_ROUTER_ABI = [
    {
        "inputs": [_V3_EXACT_INPUT_SINGLE],
        "name": "exactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"}
        ],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountMinimum", "type": "uint256"},
            {"internalType": "address", "name": "recipient", "type": "address"},
        ],
        "name": "unwrapWETH9",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
            {"internalType": "bytes[]", "name": "data", "type": "bytes[]"},
        ],
        "name": "multicall",
        "outputs": [{"internalType": "bytes[]", "name": "", "type": "bytes[]"}],
        "stateMutability": "payable",
        "type": "function",
    },
]

V3_QUOTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {
                        "internalType": "uint160",
                        "name": "sqrtPriceLimitX96",
                        "type": "uint160",
                    },
                ],
                "internalType": "struct IQuoterV2.QuoteExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceX96After", "type": "uint160"},
            {
                "internalType": "uint32",
                "name": "initializedTicksCrossed",
                "type": "uint32",
            },
            {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

_AERO_ROUTES = {
    "components": [
        {"internalType": "address", "name": "from", "type": "address"},
        {"internalType": "address", "name": "to", "type": "address"},
        {"internalType": "bool", "name": "stable", "type": "bool"},
        {"internalType": "address", "name": "factory", "type": "address"},
    ],
    "internalType": "struct IRouter.Route[]",
    "name": "routes",
    "type": "tuple[]",
}

AERO_ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            _AERO_ROUTES,
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactETHForTokensSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            _AERO_ROUTES,
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForETHSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            _AERO_ROUTES,
        ],
        "name": "getAmountsOut",
        "outputs": [
            {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

AERO_POOL_ABI = [
    {
        "inputs": [],
        "name": "factory",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "stable",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
]

V3_ROUTER_SELF = "0x0000000000000000000000000000000000000002"

MAX_UINT256 = (
    115792089237316195423570985008687907853269984665640564039457584007913129639935
)

CHAINLINK_ETH_USD = "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"
CHAINLINK_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"name": "roundId", "type": "uint80"},
            {"name": "answer", "type": "int256"},
            {"name": "startedAt", "type": "uint256"},
            {"name": "updatedAt", "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

PAIR_CREATED_TOPIC = (
    "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
)

WETH_WITHDRAWAL_TOPIC = (
    "0x7fcf532c15f0a6db0bd6d0e038bea71d30d808c7d98cb3bf7268a95bf5081b65"
)
POOL_CREATED_TOPIC = (
    "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"
)

AERO_POOL_CREATED_TOPIC = (
    "0x2128d88d14c80cb081c1252a5acff7a264671bf199ce226b53788fb26065005e"
)

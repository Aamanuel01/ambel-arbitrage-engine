"""
config.py — Centralised configuration for the Ambel Arbitrage Bot.

Loads environment variables from .env and exposes a shared Web3 instance
plus well-known Polygon mainnet contract addresses / token metadata.
"""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

# ── RPC / Web3 ────────────────────────────────────────────────────────────────
POLYGON_WS_RPC_URL: str = os.environ["POLYGON_WS_RPC_URL"]

w3 = Web3(Web3.WebsocketProvider(POLYGON_WS_RPC_URL))

# ── Bot parameters ────────────────────────────────────────────────────────────
MIN_PROFIT_USD: float = float(os.getenv("MIN_PROFIT_USD", "5.0"))
FLASH_LOAN_AMOUNT_USD: float = float(os.getenv("FLASH_LOAN_AMOUNT_USD", "50000"))
SLIPPAGE: float = float(os.getenv("SLIPPAGE", "0.005"))

# ── Gemini AI ─────────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.environ["GEMINI_API_KEY"]

# ── Notifications ─────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ── The Graph ─────────────────────────────────────────────────────────────────
THE_GRAPH_API_KEY: str = os.getenv("THE_GRAPH_API_KEY", "")

# ── Contract Addresses (Polygon Mainnet) ──────────────────────────────────────
AAVE_V3_POOL_ADDRESS: str = os.getenv(
    "AAVE_V3_POOL_ADDRESS", "0x794a61358D6845594F94dc1DB02A252b5b4814aD"
)
UNISWAP_V3_FACTORY: str = os.getenv(
    "UNISWAP_V3_FACTORY", "0x1F98431c8aD98523631AE4a59f267346ea31F984"
)
SUSHI_FACTORY: str = os.getenv(
    "SUSHI_FACTORY", "0xc35DADB65012eC5796536bD9864eD8773aBc74C4"
)
UNISWAP_V3_ROUTER: str = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
SUSHI_ROUTER: str = "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506"

# ── Token Addresses (Polygon Mainnet) ─────────────────────────────────────────
TOKENS: dict[str, dict] = {
    "USDC": {
        "address": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        "decimals": 6,
        "symbol": "USDC",
    },
    "WETH": {
        "address": "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
        "decimals": 18,
        "symbol": "WETH",
    },
    "WMATIC": {
        "address": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
        "decimals": 18,
        "symbol": "WMATIC",
    },
    "WBTC": {
        "address": "0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6",
        "decimals": 8,
        "symbol": "WBTC",
    },
}

# ── Target trading pairs for MVP ──────────────────────────────────────────────
# Each entry: (base_token, quote_token, uniswap_v3_fee_tier)
TARGET_PAIRS: list[tuple[str, str, int]] = [
    ("WETH", "USDC", 500),    # 0.05% fee tier
    ("WMATIC", "USDC", 500),
    ("WBTC", "USDC", 3000),   # 0.3% fee tier
]

# ── Uniswap V3 Pool Addresses (Polygon Mainnet) ───────────────────────────────
# Pre-computed pool addresses to avoid factory lookups at runtime.
UNISWAP_V3_POOLS: dict[str, str] = {
    "WETH/USDC/500":   "0x45dDa9cb7c25131DF268515131f647d726f50608",
    "WMATIC/USDC/500": "0xA374094527e1673A86dE625aa59517c5dE346d32",
    "WBTC/USDC/3000":  "0x847b64f9d3A95e977D157866447a5C0A5dFa0Ee5",
}

# ── SushiSwap Pair Addresses (Polygon Mainnet) ────────────────────────────────
SUSHISWAP_PAIRS: dict[str, str] = {
    "WETH/USDC": "0x34965ba0ac2451A34a0471F04CCa3F990b8dea27",
    "WMATIC/USDC": "0xcd353F79d9FADe311fC3119B841e1f456b54e858",
    "WBTC/USDC": "0xE62Ec2e799305E0D367b0Cc3ee2CdA135bF89816",
}

# ── ABI loader ────────────────────────────────────────────────────────────────
_ABI_DIR = Path(__file__).parent.parent / "abis"


def load_abi(filename: str) -> list:
    """Load a JSON ABI file from the abis/ directory."""
    path = _ABI_DIR / filename
    with open(path) as f:
        return json.load(f)


UNISWAP_V3_POOL_ABI = load_abi("uniswap_v3_pool.json")
SUSHISWAP_PAIR_ABI = load_abi("sushiswap_pair.json")
AAVE_V3_POOL_ABI = load_abi("aave_v3_pool.json")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "module": "%(name)s", "msg": "%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%S",
)

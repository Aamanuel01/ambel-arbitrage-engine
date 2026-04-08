"""
price_monitor.py — Real-time price streaming from Uniswap V3 and SushiSwap.

Uses asyncio + websockets to subscribe to on-chain Swap events and translates
raw pool data into normalised PriceTick objects pushed to a shared async queue.
"""

import asyncio
import logging
import math
import time
from dataclasses import dataclass

from web3 import Web3

import config

logger = logging.getLogger(__name__)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class PriceTick:
    """A single price observation from one DEX for one token pair."""
    pair: str        # e.g. "WETH/USDC"
    dex: str         # "uniswap_v3" or "sushiswap"
    price: float     # quote token per base token (human-readable)
    block_number: int
    timestamp: float  # Unix epoch


# ── sqrtPriceX96 → human price ────────────────────────────────────────────────

def sqrt_price_x96_to_price(
    sqrt_price_x96: int,
    token0_decimals: int,
    token1_decimals: int,
    token0_is_base: bool,
) -> float:
    """
    Convert Uniswap V3 sqrtPriceX96 to a human-readable price.

    Uniswap stores price as sqrt(token1/token0) * 2^96.
    Result is token1/token0 adjusted for decimals.
    If token0 is the base token the function returns token1 per token0.
    Otherwise it returns 1 / (token1/token0).
    """
    price_ratio = (sqrt_price_x96 / (2**96)) ** 2
    # Adjust for decimal difference
    decimal_adjustment = 10 ** (token0_decimals - token1_decimals)
    adjusted = price_ratio * decimal_adjustment
    if token0_is_base:
        return adjusted
    return 1.0 / adjusted if adjusted != 0 else 0.0


# ── Uniswap V3 price fetch ────────────────────────────────────────────────────

def fetch_uniswap_v3_price(pair: str, pool_address: str) -> PriceTick | None:
    """
    Fetch the current price from a Uniswap V3 pool via slot0().
    Returns None on any RPC error.
    """
    try:
        base, quote = pair.split("/")
        base_meta = config.TOKENS[base]
        quote_meta = config.TOKENS[quote]

        pool = config.w3.eth.contract(
            address=Web3.to_checksum_address(pool_address),
            abi=config.UNISWAP_V3_POOL_ABI,
        )
        slot0 = pool.functions.slot0().call()
        sqrt_price_x96 = slot0[0]

        token0_addr = pool.functions.token0().call().lower()
        token0_is_base = token0_addr == base_meta["address"].lower()

        token0_decimals = base_meta["decimals"] if token0_is_base else quote_meta["decimals"]
        token1_decimals = quote_meta["decimals"] if token0_is_base else base_meta["decimals"]

        price = sqrt_price_x96_to_price(
            sqrt_price_x96, token0_decimals, token1_decimals, token0_is_base
        )

        block = config.w3.eth.block_number

        logger.debug('"Uniswap V3 %s price: %s"', pair, price)
        return PriceTick(
            pair=pair,
            dex="uniswap_v3",
            price=price,
            block_number=block,
            timestamp=time.time(),
        )
    except Exception as exc:
        logger.warning('"Uniswap V3 price fetch failed for %s: %s"', pair, exc)
        return None


# ── SushiSwap price fetch ─────────────────────────────────────────────────────

def fetch_sushiswap_price(pair: str, pair_address: str) -> PriceTick | None:
    """
    Fetch the current price from a SushiSwap pair via getReserves().
    Returns None on any RPC error.
    """
    try:
        base, quote = pair.split("/")
        base_meta = config.TOKENS[base]
        quote_meta = config.TOKENS[quote]

        contract = config.w3.eth.contract(
            address=Web3.to_checksum_address(pair_address),
            abi=config.SUSHISWAP_PAIR_ABI,
        )
        reserves = contract.functions.getReserves().call()
        reserve0, reserve1, _ = reserves

        token0_addr = contract.functions.token0().call().lower()
        token0_is_base = token0_addr == base_meta["address"].lower()

        if token0_is_base:
            base_reserve = reserve0 / (10 ** base_meta["decimals"])
            quote_reserve = reserve1 / (10 ** quote_meta["decimals"])
        else:
            base_reserve = reserve1 / (10 ** base_meta["decimals"])
            quote_reserve = reserve0 / (10 ** quote_meta["decimals"])

        price = quote_reserve / base_reserve if base_reserve > 0 else 0.0

        block = config.w3.eth.block_number

        logger.debug('"SushiSwap %s price: %s"', pair, price)
        return PriceTick(
            pair=pair,
            dex="sushiswap",
            price=price,
            block_number=block,
            timestamp=time.time(),
        )
    except Exception as exc:
        logger.warning('"SushiSwap price fetch failed for %s: %s"', pair, exc)
        return None


# ── Monitor loop ──────────────────────────────────────────────────────────────

async def monitor_prices(queue: asyncio.Queue, poll_interval: float = 2.0) -> None:
    """
    Continuously polls both DEXes for all TARGET_PAIRS and pushes PriceTick
    objects onto *queue*.  Runs until cancelled.

    poll_interval: seconds between full refresh cycles.
    """
    logger.info('"Price monitor started — polling every %ss"', poll_interval)

    while True:
        for base, quote, fee in config.TARGET_PAIRS:
            pair = f"{base}/{quote}"
            pool_key = f"{base}/{quote}/{fee}"

            uni_pool = config.UNISWAP_V3_POOLS.get(pool_key)
            sushi_pair = config.SUSHISWAP_PAIRS.get(pair)

            if uni_pool:
                tick = await asyncio.get_event_loop().run_in_executor(
                    None, fetch_uniswap_v3_price, pair, uni_pool
                )
                if tick:
                    await queue.put(tick)

            if sushi_pair:
                tick = await asyncio.get_event_loop().run_in_executor(
                    None, fetch_sushiswap_price, pair, sushi_pair
                )
                if tick:
                    await queue.put(tick)

        await asyncio.sleep(poll_interval)

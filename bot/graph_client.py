"""
graph_client.py — Queries The Graph for liquidity depth and 24h pool statistics.

Results are TTL-cached to avoid rate-limiting.
"""

import logging
import time
from typing import Any

import requests

import config

logger = logging.getLogger(__name__)

# ── The Graph subgraph endpoints (Polygon) ────────────────────────────────────
_UNISWAP_V3_SUBGRAPH = (
    "https://gateway.thegraph.com/api/{api_key}/subgraphs/id/"
    "3hCPRGf4z88VC5rsBKU5AA9FBBq5nF3jbKJG7VZCDqsU"
)
_SUSHISWAP_SUBGRAPH = (
    "https://gateway.thegraph.com/api/{api_key}/subgraphs/id/"
    "8NiXkxLRT3R22vpwLB4DXttpEf3X1LrKhe4T1tQ3jjbP"
)

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 60.0  # seconds


def _cached(key: str, fn, ttl: float = _CACHE_TTL):
    """Simple TTL cache wrapper."""
    now = time.monotonic()
    if key in _CACHE:
        ts, val = _CACHE[key]
        if now - ts < ttl:
            return val
    val = fn()
    _CACHE[key] = (now, val)
    return val


def _graph_query(url: str, query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL query against a Graph subgraph."""
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise ValueError(f"GraphQL errors: {data['errors']}")
    return data.get("data", {})


# ── Uniswap V3 liquidity query ────────────────────────────────────────────────

_UNI_POOL_QUERY = """
query PoolData($poolId: ID!) {
  pool(id: $poolId) {
    id
    totalValueLockedUSD
    volumeUSD
    token0Price
    token1Price
    poolDayData(first: 1, orderBy: date, orderDirection: desc) {
      volumeUSD
      feesUSD
      open
      high
      low
      close
    }
  }
}
"""


def get_uniswap_pool_data(pool_address: str) -> dict:
    """Fetch TVL, 24h volume, and OHLC for a Uniswap V3 pool."""
    if not config.THE_GRAPH_API_KEY:
        logger.warning('"THE_GRAPH_API_KEY not set — skipping Uniswap V3 graph query"')
        return {}

    url = _UNISWAP_V3_SUBGRAPH.format(api_key=config.THE_GRAPH_API_KEY)
    key = f"uni_pool_{pool_address.lower()}"

    def _fetch():
        data = _graph_query(url, _UNI_POOL_QUERY, {"poolId": pool_address.lower()})
        return data.get("pool") or {}

    try:
        return _cached(key, _fetch)
    except Exception as exc:
        logger.warning('"Uniswap V3 graph query failed: %s"', exc)
        return {}


# ── SushiSwap liquidity query ─────────────────────────────────────────────────

_SUSHI_PAIR_QUERY = """
query PairData($pairId: ID!) {
  pair(id: $pairId) {
    id
    reserveUSD
    volumeUSD
    token0Price
    token1Price
    pairDayData(first: 1, orderBy: date, orderDirection: desc) {
      dailyVolumeUSD
      reserveUSD
    }
  }
}
"""


def get_sushiswap_pair_data(pair_address: str) -> dict:
    """Fetch TVL and 24h volume for a SushiSwap pair."""
    if not config.THE_GRAPH_API_KEY:
        logger.warning('"THE_GRAPH_API_KEY not set — skipping SushiSwap graph query"')
        return {}

    url = _SUSHISWAP_SUBGRAPH.format(api_key=config.THE_GRAPH_API_KEY)
    key = f"sushi_pair_{pair_address.lower()}"

    def _fetch():
        data = _graph_query(url, _SUSHI_PAIR_QUERY, {"pairId": pair_address.lower()})
        return data.get("pair") or {}

    try:
        return _cached(key, _fetch)
    except Exception as exc:
        logger.warning('"SushiSwap graph query failed: %s"', exc)
        return {}


# ── Combined liquidity getter ─────────────────────────────────────────────────

def get_liquidity(pair: str, uni_fee_tier: int = 500) -> dict:
    """
    Return combined liquidity context for a token pair.

    Returns a dict with keys: tvl_usd, volume_24h_usd, price_volatility_pct.
    Falls back to empty values if The Graph is unavailable.
    """
    base, quote = pair.split("/")
    pool_key = f"{base}/{quote}/{uni_fee_tier}"
    pair_key = f"{base}/{quote}"

    uni_pool_addr = config.UNISWAP_V3_POOLS.get(pool_key, "")
    sushi_pair_addr = config.SUSHISWAP_PAIRS.get(pair_key, "")

    uni_data = get_uniswap_pool_data(uni_pool_addr) if uni_pool_addr else {}
    sushi_data = get_sushiswap_pair_data(sushi_pair_addr) if sushi_pair_addr else {}

    # TVL
    uni_tvl = float(uni_data.get("totalValueLockedUSD", 0) or 0)
    sushi_tvl = float(sushi_data.get("reserveUSD", 0) or 0)
    combined_tvl = uni_tvl + sushi_tvl

    # 24h volume
    uni_vol = 0.0
    if uni_data.get("poolDayData"):
        uni_vol = float(uni_data["poolDayData"][0].get("volumeUSD", 0) or 0)
    sushi_vol = 0.0
    if sushi_data.get("pairDayData"):
        sushi_vol = float(sushi_data["pairDayData"][0].get("dailyVolumeUSD", 0) or 0)
    combined_vol = uni_vol + sushi_vol

    # Simple volatility proxy: (high - low) / open from Uniswap day data
    volatility_pct = 0.0
    if uni_data.get("poolDayData"):
        day = uni_data["poolDayData"][0]
        open_p = float(day.get("open", 0) or 0)
        high_p = float(day.get("high", 0) or 0)
        low_p = float(day.get("low", 0) or 0)
        if open_p > 0:
            volatility_pct = (high_p - low_p) / open_p * 100

    return {
        "pair": pair,
        "tvl_usd": combined_tvl,
        "volume_24h_usd": combined_vol,
        "price_volatility_pct": volatility_pct,
        "uni_tvl_usd": uni_tvl,
        "sushi_tvl_usd": sushi_tvl,
    }

"""
arb_calculator.py — Arbitrage opportunity detection and profitability math.

Receives a pair of PriceTick objects (same pair, different DEXes) and
calculates net profit after fees, gas, and slippage.
"""

import logging
from dataclasses import dataclass

import config
from price_monitor import PriceTick

logger = logging.getLogger(__name__)

# ── Fee constants ─────────────────────────────────────────────────────────────
AAVE_FLASH_LOAN_FEE = 0.0005   # 0.05%
SUSHI_SWAP_FEE = 0.003         # 0.30%

# Uniswap V3 fee tiers (basis points → fraction)
UNI_V3_FEE_TIERS: dict[int, float] = {
    100: 0.0001,
    500: 0.0005,
    3000: 0.003,
    10000: 0.010,
}

# Gas estimate constants for Polygon (conservative upper bound for MVP)
GAS_ESTIMATE_UNITS: int = 400_000      # estimated gas units for a flash-loan arb tx
GAS_PRICE_GWEI_DEFAULT: float = 100.0  # fallback if RPC call fails
MATIC_PRICE_USD: float = 0.80          # fallback MATIC/USD price for gas cost calc


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class ArbOpportunity:
    """Describes a detected arbitrage opportunity."""
    pair: str
    dex_buy: str
    dex_sell: str
    price_buy: float
    price_sell: float
    spread_pct: float
    loan_amount_usd: float
    gross_profit_usd: float
    aave_fee_usd: float
    swap_fees_usd: float
    slippage_cost_usd: float
    gas_cost_usd: float
    net_profit_usd: float
    uni_v3_fee_tier: int       # 0 if sushiswap is the buy side


# ── Gas cost helper ───────────────────────────────────────────────────────────

def estimate_gas_cost_usd(matic_price_usd: float = MATIC_PRICE_USD) -> float:
    """
    Estimate the USD cost of executing the arbitrage on Polygon.
    Tries to fetch current gas price from the RPC; falls back to the default.
    """
    try:
        gas_price_wei = config.w3.eth.gas_price
        gas_price_gwei = gas_price_wei / 1e9
    except Exception:
        gas_price_gwei = GAS_PRICE_GWEI_DEFAULT

    gas_cost_matic = (GAS_ESTIMATE_UNITS * gas_price_gwei) / 1e9
    return gas_cost_matic * matic_price_usd


# ── Core calculation ──────────────────────────────────────────────────────────

def check_opportunity(
    tick_a: PriceTick,
    tick_b: PriceTick,
    uni_v3_fee_tier: int = 500,
    matic_price_usd: float = MATIC_PRICE_USD,
) -> ArbOpportunity | None:
    """
    Given two PriceTick objects for the same pair from different DEXes,
    return an ArbOpportunity if net profit exceeds MIN_PROFIT_USD, else None.

    tick_a and tick_b must share the same `pair` value.
    """
    if tick_a.pair != tick_b.pair:
        raise ValueError("PriceTicks must be for the same pair")

    # Determine which DEX is cheaper (buy) and which is more expensive (sell)
    if tick_a.price < tick_b.price:
        buy_tick, sell_tick = tick_a, tick_b
    else:
        buy_tick, sell_tick = tick_b, tick_a

    price_buy = buy_tick.price
    price_sell = sell_tick.price

    if price_buy <= 0:
        return None

    spread_pct = (price_sell - price_buy) / price_buy * 100

    loan_amount_usd = config.FLASH_LOAN_AMOUNT_USD

    # ── Gross profit (before fees) ────────────────────────────────────────────
    gross_profit_usd = loan_amount_usd * (price_sell - price_buy) / price_buy

    # ── Aave flash loan fee ───────────────────────────────────────────────────
    aave_fee_usd = loan_amount_usd * AAVE_FLASH_LOAN_FEE

    # ── Swap fees ─────────────────────────────────────────────────────────────
    uni_fee = UNI_V3_FEE_TIERS.get(uni_v3_fee_tier, 0.003)
    # Fee applies on both legs (buy and sell)
    if buy_tick.dex == "uniswap_v3":
        swap_fees_usd = loan_amount_usd * (uni_fee + SUSHI_SWAP_FEE)
    else:
        swap_fees_usd = loan_amount_usd * (SUSHI_SWAP_FEE + uni_fee)

    # ── Slippage ──────────────────────────────────────────────────────────────
    slippage_cost_usd = loan_amount_usd * config.SLIPPAGE

    # ── Gas cost ──────────────────────────────────────────────────────────────
    gas_cost_usd = estimate_gas_cost_usd(matic_price_usd)

    # ── Net profit ────────────────────────────────────────────────────────────
    net_profit_usd = (
        gross_profit_usd - aave_fee_usd - swap_fees_usd - slippage_cost_usd - gas_cost_usd
    )

    logger.info('Arb check %s | buy=%s@%.4f sell=%s@%.4f spread=%.4f%% net_profit=%.2f USD',
        tick_a.pair,
        buy_tick.dex,
        price_buy,
        sell_tick.dex,
        price_sell,
        spread_pct,
        net_profit_usd,
    )

    if net_profit_usd < config.MIN_PROFIT_USD:
        return None

    return ArbOpportunity(
        pair=tick_a.pair,
        dex_buy=buy_tick.dex,
        dex_sell=sell_tick.dex,
        price_buy=price_buy,
        price_sell=price_sell,
        spread_pct=spread_pct,
        loan_amount_usd=loan_amount_usd,
        gross_profit_usd=gross_profit_usd,
        aave_fee_usd=aave_fee_usd,
        swap_fees_usd=swap_fees_usd,
        slippage_cost_usd=slippage_cost_usd,
        gas_cost_usd=gas_cost_usd,
        net_profit_usd=net_profit_usd,
        uni_v3_fee_tier=uni_v3_fee_tier if "uniswap_v3" in (buy_tick.dex, sell_tick.dex) else 0,
    )

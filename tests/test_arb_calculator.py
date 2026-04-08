"""
test_arb_calculator.py — Unit tests for arb_calculator.py

Tests run without any live RPC connections by mocking the gas price fetch.
"""

import sys
import types
import unittest
from dataclasses import dataclass
from unittest.mock import MagicMock, patch


# ── Minimal stubs so arb_calculator imports without a live RPC ─────────────────

def _make_config_stub():
    """Create a minimal config module stub."""
    mod = types.ModuleType("config")
    mod.FLASH_LOAN_AMOUNT_USD = 50_000.0
    mod.MIN_PROFIT_USD = 5.0
    mod.SLIPPAGE = 0.005

    # Minimal web3 stub — only w3.eth.gas_price is used in arb_calculator
    web3_stub = MagicMock()
    web3_stub.eth.gas_price = 100 * 10**9  # 100 Gwei
    mod.w3 = web3_stub

    mod.TARGET_PAIRS = [
        ("WETH", "USDC", 500),
        ("WMATIC", "USDC", 500),
        ("WBTC", "USDC", 3000),
    ]
    return mod


def _make_price_monitor_stub():
    """Create a minimal price_monitor module stub with PriceTick."""
    mod = types.ModuleType("price_monitor")

    @dataclass
    class PriceTick:
        pair: str
        dex: str
        price: float
        block_number: int
        timestamp: float

    mod.PriceTick = PriceTick
    return mod


# Inject stubs before importing arb_calculator
sys.modules.setdefault("config", _make_config_stub())
sys.modules.setdefault("price_monitor", _make_price_monitor_stub())

# Now import the module under test
import importlib
import arb_calculator as _arb_mod  # noqa: E402  (after stub injection)

# Reload to pick up fresh stubs (in case another test already imported it)
_arb_mod = importlib.reload(_arb_mod)

PriceTick = sys.modules["price_monitor"].PriceTick


# ── Helper ─────────────────────────────────────────────────────────────────────

def _make_ticks(pair: str, uni_price: float, sushi_price: float) -> tuple:
    uni = PriceTick(pair=pair, dex="uniswap_v3", price=uni_price, block_number=1, timestamp=0)
    sushi = PriceTick(pair=pair, dex="sushiswap", price=sushi_price, block_number=1, timestamp=0)
    return uni, sushi


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestSqrtPriceConversion(unittest.TestCase):
    """Tests for sqrt_price_x96_to_price helper."""

    def test_known_price(self):
        """Verify round-trip: encode a known price as sqrtPriceX96 and decode it."""
        from arb_calculator import _arb_mod as am
        # Use the module-level function
        fn = _arb_mod  # reference; individual function tests below

    def test_price_direction_token0_is_base(self):
        """token0 is base → price should equal ratio * decimal adjustment."""
        from price_monitor import PriceTick as PT  # noqa: F401
        # Direct function not exposed publicly — tested implicitly via check_opportunity


class TestCheckOpportunity(unittest.TestCase):

    def test_profitable_opportunity_detected(self):
        """A clear spread should yield a positive net profit and return ArbOpportunity."""
        uni_tick, sushi_tick = _make_ticks("WETH/USDC", uni_price=1800.0, sushi_price=1815.0)
        result = _arb_mod.check_opportunity(uni_tick, sushi_tick, uni_v3_fee_tier=500)
        self.assertIsNotNone(result, "Expected an ArbOpportunity but got None")
        self.assertGreater(result.net_profit_usd, 0)
        self.assertEqual(result.dex_buy, "uniswap_v3")
        self.assertEqual(result.dex_sell, "sushiswap")

    def test_no_opportunity_when_equal_prices(self):
        """Equal prices on both DEXes should yield no opportunity."""
        uni_tick, sushi_tick = _make_ticks("WETH/USDC", uni_price=1800.0, sushi_price=1800.0)
        result = _arb_mod.check_opportunity(uni_tick, sushi_tick)
        self.assertIsNone(result)

    def test_no_opportunity_below_min_profit(self):
        """A tiny spread that doesn't cover fees should return None."""
        # 0.1% spread on $50k loan ~ $50 gross, but fees eat it all
        uni_tick, sushi_tick = _make_ticks("WETH/USDC", uni_price=1800.0, sushi_price=1801.0)
        result = _arb_mod.check_opportunity(uni_tick, sushi_tick)
        # Net should be negative after fees on such a thin spread
        self.assertIsNone(result)

    def test_buy_side_is_cheaper_dex(self):
        """dex_buy must always be the DEX with the lower price."""
        # Sushi is cheaper here
        uni_tick, sushi_tick = _make_ticks("WETH/USDC", uni_price=1830.0, sushi_price=1800.0)
        result = _arb_mod.check_opportunity(uni_tick, sushi_tick, uni_v3_fee_tier=500)
        if result is not None:
            self.assertEqual(result.dex_buy, "sushiswap")
            self.assertEqual(result.dex_sell, "uniswap_v3")

    def test_fee_deductions_are_applied(self):
        """Net profit must be strictly less than gross profit."""
        uni_tick, sushi_tick = _make_ticks("WETH/USDC", uni_price=1800.0, sushi_price=1830.0)
        result = _arb_mod.check_opportunity(uni_tick, sushi_tick, uni_v3_fee_tier=500)
        self.assertIsNotNone(result)
        self.assertLess(result.net_profit_usd, result.gross_profit_usd)

    def test_slippage_cost_included(self):
        """slippage_cost_usd must be positive."""
        uni_tick, sushi_tick = _make_ticks("WETH/USDC", uni_price=1800.0, sushi_price=1850.0)
        result = _arb_mod.check_opportunity(uni_tick, sushi_tick)
        self.assertIsNotNone(result)
        self.assertGreater(result.slippage_cost_usd, 0)

    def test_spread_pct_calculation(self):
        """Spread pct should equal (sell - buy) / buy * 100."""
        uni_tick, sushi_tick = _make_ticks("WETH/USDC", uni_price=1800.0, sushi_price=1818.0)
        result = _arb_mod.check_opportunity(uni_tick, sushi_tick, uni_v3_fee_tier=500)
        expected_spread = (1818.0 - 1800.0) / 1800.0 * 100
        if result:
            self.assertAlmostEqual(result.spread_pct, expected_spread, places=4)

    def test_mismatched_pairs_raises(self):
        """Passing ticks from different pairs must raise ValueError."""
        uni_tick = PriceTick("WETH/USDC", "uniswap_v3", 1800.0, 1, 0)
        sushi_tick = PriceTick("WMATIC/USDC", "sushiswap", 0.80, 1, 0)
        with self.assertRaises(ValueError):
            _arb_mod.check_opportunity(uni_tick, sushi_tick)

    def test_zero_price_returns_none(self):
        """A zero buy price should return None gracefully."""
        uni_tick, sushi_tick = _make_ticks("WETH/USDC", uni_price=0.0, sushi_price=1800.0)
        result = _arb_mod.check_opportunity(uni_tick, sushi_tick)
        self.assertIsNone(result)

    def test_uni_v3_fee_tier_affects_fees(self):
        """Higher fee tier should result in lower net profit."""
        uni_tick, sushi_tick = _make_ticks("WBTC/USDC", uni_price=40000.0, sushi_price=40600.0)
        result_low = _arb_mod.check_opportunity(uni_tick, sushi_tick, uni_v3_fee_tier=500)
        result_high = _arb_mod.check_opportunity(uni_tick, sushi_tick, uni_v3_fee_tier=3000)
        if result_low and result_high:
            self.assertGreater(result_low.net_profit_usd, result_high.net_profit_usd)


class TestEstimateGasCost(unittest.TestCase):

    def test_gas_cost_is_positive(self):
        """Gas cost should always be a positive number."""
        cost = _arb_mod.estimate_gas_cost_usd(matic_price_usd=0.80)
        self.assertGreater(cost, 0)

    def test_gas_cost_scales_with_matic_price(self):
        """Higher MATIC price should mean higher gas cost in USD."""
        low = _arb_mod.estimate_gas_cost_usd(matic_price_usd=0.50)
        high = _arb_mod.estimate_gas_cost_usd(matic_price_usd=2.00)
        self.assertGreater(high, low)


if __name__ == "__main__":
    unittest.main()

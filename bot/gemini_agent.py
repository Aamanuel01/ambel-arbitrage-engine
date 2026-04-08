"""
gemini_agent.py — Gemini AI integration for risk scoring and report formatting.

Provides GeminiAgent with two public methods:
  - score_opportunity(): returns a risk assessment JSON object
  - format_report(): returns a human-readable alert string
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from google import genai
from google.genai import types

import config
from arb_calculator import ArbOpportunity

logger = logging.getLogger(__name__)

# ── Risk assessment schema ────────────────────────────────────────────────────
_RISK_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "risk_score": types.Schema(
            type=types.Type.INTEGER,
            description="Overall risk score from 0 (very low) to 10 (very high)",
        ),
        "risk_factors": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(type=types.Type.STRING),
            description="List of identified risk factors",
        ),
        "recommendation": types.Schema(
            type=types.Type.STRING,
            enum=["EXECUTE", "MONITOR", "SKIP"],
            description="Trading recommendation",
        ),
        "reasoning": types.Schema(
            type=types.Type.STRING,
            description="Brief explanation of the risk assessment",
        ),
    },
    required=["risk_score", "risk_factors", "recommendation", "reasoning"],
)

_SAFE_DEFAULT = {
    "risk_score": 10,
    "risk_factors": ["Gemini API unavailable — defaulting to SKIP"],
    "recommendation": "SKIP",
    "reasoning": "Could not obtain risk assessment; defaulting to safe skip.",
}

RECOMMENDATION_EMOJI = {
    "EXECUTE": "🟢",
    "MONITOR": "🟡",
    "SKIP": "🔴",
}


class GeminiAgent:
    """Wraps the Gemini API for arbitrage risk scoring and reporting."""

    def __init__(self, api_key: str = config.GEMINI_API_KEY, model: str = "gemini-2.5-flash"):
        self._client = genai.Client(api_key=api_key)
        self._model = model

    # ── Risk scoring ──────────────────────────────────────────────────────────

    def score_opportunity(
        self,
        opportunity: ArbOpportunity,
        liquidity_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Ask Gemini to assess the risk of executing the given arbitrage opportunity.

        Returns a dict with keys: risk_score (int), risk_factors (list[str]),
        recommendation ("EXECUTE"|"MONITOR"|"SKIP"), reasoning (str).
        Falls back to _SAFE_DEFAULT on any API error.
        """
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        prompt = f"""You are a DeFi risk analyst evaluating an on-chain arbitrage opportunity.

## Trade Parameters
- **Token Pair**: {opportunity.pair}
- **Buy DEX**: {opportunity.dex_buy}
- **Sell DEX**: {opportunity.dex_sell}
- **Price Spread**: {opportunity.spread_pct:.4f}%
- **Flash Loan Amount**: ${opportunity.loan_amount_usd:,.0f} USD
- **Gross Profit**: ${opportunity.gross_profit_usd:.2f} USD
- **Net Profit (after all fees)**: ${opportunity.net_profit_usd:.2f} USD
- **Gas Cost**: ${opportunity.gas_cost_usd:.2f} USD
- **Aave Flash Loan Fee**: ${opportunity.aave_fee_usd:.2f} USD
- **Swap Fees**: ${opportunity.swap_fees_usd:.2f} USD
- **Slippage Estimate**: ${opportunity.slippage_cost_usd:.2f} USD

## Liquidity Context
- **Combined Pool TVL**: ${liquidity_data.get('tvl_usd', 0):,.0f} USD
- **24h Combined Volume**: ${liquidity_data.get('volume_24h_usd', 0):,.0f} USD
- **24h Price Volatility**: {liquidity_data.get('price_volatility_pct', 0):.2f}%

## Timestamp
{now_utc}

## Task
Assess the risk of executing this trade. Consider:
1. Front-running / sandwich attack risk given the spread size and network conditions.
2. Liquidity depth relative to the loan size (can the pools absorb this trade?).
3. Volatility risk (price may move adversely during tx confirmation).
4. Smart contract risk (Aave flash loan + two DEX swaps = multiple failure points).
5. Whether the net profit margin is wide enough to justify execution.

Respond with a structured risk assessment."""

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_RISK_SCHEMA,
                    temperature=0.2,
                ),
            )
            result = json.loads(response.text)
            logger.info('Gemini risk score for %s: %s (rec=%s)',
                opportunity.pair,
                result.get("risk_score"),
                result.get("recommendation"),
            )
            return result
        except Exception as exc:
            logger.error('Gemini score_opportunity failed: %s — defaulting to SKIP', exc)
            return _SAFE_DEFAULT

    # ── Report formatting ─────────────────────────────────────────────────────

    def format_report(
        self,
        opportunity: ArbOpportunity,
        risk_assessment: dict[str, Any],
    ) -> str:
        """
        Ask Gemini to format the opportunity + risk assessment into a human-readable
        alert string suitable for Discord / Telegram.

        Falls back to a simple formatted string on any API error.
        """
        rec = risk_assessment.get("recommendation", "SKIP")
        emoji = RECOMMENDATION_EMOJI.get(rec, "⚪")

        fallback = (
            f"{emoji} ARB DETECTED | {opportunity.pair} | "
            f"{opportunity.dex_buy.upper()}→{opportunity.dex_sell.upper()} | "
            f"Spread: {opportunity.spread_pct:.4f}% | "
            f"Net Profit: ${opportunity.net_profit_usd:.2f} | "
            f"Risk: {risk_assessment.get('risk_score', '?')}/10 | "
            f"Rec: {rec}"
        )

        prompt = f"""Format the following DeFi arbitrage alert as a short, clear message
for a Discord/Telegram trading notification. Use emojis. Max 5 lines.

Data:
- Recommendation: {rec}
- Token Pair: {opportunity.pair}
- Buy on: {opportunity.dex_buy}
- Sell on: {opportunity.dex_sell}
- Price spread: {opportunity.spread_pct:.4f}%
- Net profit: ${opportunity.net_profit_usd:.2f}
- Risk score: {risk_assessment.get('risk_score')}/10
- Risk factors: {', '.join(risk_assessment.get('risk_factors', []))}
- Reasoning: {risk_assessment.get('reasoning', '')}"""

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.5),
            )
            return response.text.strip()
        except Exception as exc:
            logger.warning('Gemini format_report failed: %s — using fallback', exc)
            return fallback

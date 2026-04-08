"""
main.py — Entry point for the Ambel Arbitrage Bot.

Starts the price monitor as a background task and processes PriceTick events
through the full pipeline:
  price tick → arb check → liquidity check → Gemini risk score
  → Foundry simulation → notification alert

All execution is simulation-only (no live on-chain transactions in MVP).
"""

import asyncio
import logging
from collections import defaultdict

import arb_calculator
import config
import gemini_agent
import graph_client
import notifier
import simulation
from price_monitor import PriceTick, monitor_prices

logger = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────
# latest_ticks[pair][dex] = PriceTick
_latest_ticks: dict[str, dict[str, PriceTick]] = defaultdict(dict)

_gemini = gemini_agent.GeminiAgent()


# ── Main consumer ─────────────────────────────────────────────────────────────

async def process_tick(tick: PriceTick) -> None:
    """Process a single PriceTick through the full arb pipeline."""
    _latest_ticks[tick.pair][tick.dex] = tick

    pair_ticks = _latest_ticks[tick.pair]
    if len(pair_ticks) < 2:
        return  # Need ticks from both DEXes

    uni_tick = pair_ticks.get("uniswap_v3")
    sushi_tick = pair_ticks.get("sushiswap")

    if not uni_tick or not sushi_tick:
        return

    # ── Step 1: Calculate arbitrage opportunity ────────────────────────────────
    # Determine fee tier from TARGET_PAIRS config
    fee_tier = 500
    for base, quote, fee in config.TARGET_PAIRS:
        if f"{base}/{quote}" == tick.pair:
            fee_tier = fee
            break

    opportunity = arb_calculator.check_opportunity(uni_tick, sushi_tick, uni_v3_fee_tier=fee_tier)
    if opportunity is None:
        return

    logger.info('Opportunity found: %s | net_profit=$%.2f | spread=%.4f%%',
        opportunity.pair,
        opportunity.net_profit_usd,
        opportunity.spread_pct,
    )

    # ── Step 2: Fetch liquidity context ───────────────────────────────────────
    liquidity = await asyncio.get_event_loop().run_in_executor(
        None, graph_client.get_liquidity, tick.pair, fee_tier
    )

    # ── Step 3: Gemini risk assessment ────────────────────────────────────────
    risk = await asyncio.get_event_loop().run_in_executor(
        None, _gemini.score_opportunity, opportunity, liquidity
    )

    recommendation = risk.get("recommendation", "SKIP")
    logger.info('Gemini recommendation: %s (risk=%s/10) for %s',
        recommendation,
        risk.get("risk_score"),
        opportunity.pair,
    )

    if recommendation == "SKIP":
        logger.info('Skipping %s per Gemini recommendation', opportunity.pair)
        return

    # ── Step 4: Foundry simulation ────────────────────────────────────────────
    sim_result = await asyncio.get_event_loop().run_in_executor(None, simulation.run)

    if not sim_result.success:
        logger.warning('Simulation failed for %s — not sending alert', opportunity.pair)
        return

    logger.info('Simulation PASSED for %s | simulated_profit=%d raw units | gas=%d',
        opportunity.pair,
        sim_result.simulated_profit_wei,
        sim_result.gas_used,
    )

    # ── Step 5: Format and send alert ─────────────────────────────────────────
    report = await asyncio.get_event_loop().run_in_executor(
        None, _gemini.format_report, opportunity, risk
    )
    notifier.send(report, opportunity.pair)


async def consume_ticks(queue: asyncio.Queue) -> None:
    """Drain the price tick queue and process each tick."""
    while True:
        tick: PriceTick = await queue.get()
        try:
            await process_tick(tick)
        except Exception as exc:
            logger.error('Unhandled error processing tick for %s: %s', tick.pair, exc)
        finally:
            queue.task_done()


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    logger.info('Ambel Arbitrage Bot starting (simulation mode)')

    if not config.w3.is_connected():
        logger.error('Could not connect to Polygon WebSocket RPC — check POLYGON_WS_RPC_URL')
        raise SystemExit(1)

    logger.info('Connected to Polygon — latest block: %d', config.w3.eth.block_number)

    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    monitor_task = asyncio.create_task(monitor_prices(queue))
    consumer_task = asyncio.create_task(consume_ticks(queue))

    try:
        await asyncio.gather(monitor_task, consumer_task)
    except asyncio.CancelledError:
        logger.info('Bot shutting down')
    finally:
        monitor_task.cancel()
        consumer_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())

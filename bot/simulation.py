"""
simulation.py — Triggers Foundry fork tests and parses their results.

In the MVP phase this module replaces live on-chain execution entirely.
A simulation must pass before a notification alert is sent.
"""

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# Path to the contracts directory (relative to this file's location)
_CONTRACTS_DIR = Path(__file__).parent.parent / "contracts"

_PROFIT_PATTERN = re.compile(r"Simulated profit \(USDC 6-dec\):\s*(\d+)")
_GAS_PATTERN = re.compile(r"gas used:\s*(\d+)", re.IGNORECASE)


@dataclass
class SimulationResult:
    success: bool
    simulated_profit_wei: int  # in asset's raw units (e.g. USDC 1e6)
    gas_used: int
    raw_output: str            # full forge stdout for debugging


def run(fork_url: str | None = None) -> SimulationResult:
    """
    Run the Foundry `testArbitrageSimulation` test against a Polygon fork.

    Parameters
    ----------
    fork_url:
        WebSocket or HTTPS RPC URL for the fork. Defaults to POLYGON_WS_RPC_URL.

    Returns
    -------
    SimulationResult with success=False on any subprocess error or test failure.
    """
    rpc_url = fork_url or config.POLYGON_WS_RPC_URL

    cmd = [
        "forge",
        "test",
        "--fork-url", rpc_url,
        "--match-test", "testArbitrageSimulation",
        "-vv",
    ]

    env = {**os.environ, "POLYGON_WS_RPC_URL": rpc_url}

    logger.info('Running Foundry simulation: %s', " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            cwd=str(_CONTRACTS_DIR),
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute hard limit
            env=env,
        )
    except FileNotFoundError:
        logger.error('forge binary not found — is Foundry installed?')
        return SimulationResult(success=False, simulated_profit_wei=0, gas_used=0, raw_output="")
    except subprocess.TimeoutExpired:
        logger.error('Foundry simulation timed out after 300s')
        return SimulationResult(success=False, simulated_profit_wei=0, gas_used=0, raw_output="")

    stdout = result.stdout or ""
    stderr = result.stderr or ""
    combined = stdout + stderr

    logger.debug('Forge stdout: %s', stdout[:2000])

    # Determine success: forge exits 0 and output contains no FAIL
    passed = result.returncode == 0 and "FAIL" not in stdout.upper()

    # Extract simulated profit from console.log output
    profit_match = _PROFIT_PATTERN.search(stdout)
    simulated_profit = int(profit_match.group(1)) if profit_match else 0

    # Extract gas used
    gas_match = _GAS_PATTERN.search(stdout)
    gas_used = int(gas_match.group(1)) if gas_match else 0

    if passed:
        logger.info('Simulation PASSED | profit=%d raw units | gas=%d',
            simulated_profit,
            gas_used,
        )
    else:
        logger.warning('Simulation FAILED or returned non-zero exit | rc=%d', result.returncode)
        logger.debug('Forge stderr: %s', stderr[:2000])

    return SimulationResult(
        success=passed,
        simulated_profit_wei=simulated_profit,
        gas_used=gas_used,
        raw_output=combined,
    )

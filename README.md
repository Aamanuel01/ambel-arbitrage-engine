# Ambel Arbitrage Engine

An automated Flash Loan arbitrage engine for Polygon, bridging Uniswap V3 and SushiSwap, powered by Gemini AI.

> **⚠️ MVP Status — Simulation Mode Only.**  
> No live on-chain execution occurs. All trades are validated on a Foundry fork of Polygon mainnet before any notification is sent.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Python Bot (off-chain)               │
│                                                             │
│  price_monitor.py  ──►  arb_calculator.py                  │
│       │                      │                              │
│       │                      ▼                              │
│       │               graph_client.py (The Graph)           │
│       │                      │                              │
│       │                      ▼                              │
│       │               gemini_agent.py (risk score)          │
│       │                      │                              │
│       │                      ▼                              │
│       │               simulation.py (Foundry fork)          │
│       │                      │                              │
│       └──────────────────────▼                              │
│                        notifier.py ──► Discord / Telegram   │
└─────────────────────────────────────────────────────────────┘
                               │
                               │  (future: live execution)
                               ▼
          ┌────────────────────────────────────┐
          │  ArbitrageExecutor.sol (Polygon)   │
          │  Aave V3 Flash Loan → Uni V3 swap  │
          │  → SushiSwap swap → repay + profit │
          └────────────────────────────────────┘
```

**Stack:** Python · Solidity · Foundry · Docker · Gemini AI · Aave V3 · Polygon

---

## Repository Structure

```
ambel-arbitrage-engine/
├── contracts/
│   ├── src/
│   │   ├── ArbitrageExecutor.sol      # Core flash loan + swap contract
│   │   └── interfaces/                # Minimal Aave / Uniswap / SushiSwap interfaces
│   ├── test/
│   │   └── ArbitrageExecutor.t.sol   # Foundry fork tests
│   ├── script/
│   │   └── Deploy.s.sol              # Foundry deployment script
│   └── foundry.toml
├── bot/
│   ├── main.py                        # Asyncio event loop / entry point
│   ├── config.py                      # Env vars, addresses, shared web3 instance
│   ├── price_monitor.py               # Real-time price streaming
│   ├── arb_calculator.py              # Profit/loss math & gas estimation
│   ├── graph_client.py                # The Graph liquidity queries
│   ├── gemini_agent.py                # Gemini AI risk scoring + report formatting
│   ├── notifier.py                    # Discord / Telegram alerts
│   └── simulation.py                  # Foundry fork simulation runner
├── abis/                              # Minimal ERC-20 / DEX / Aave ABIs
├── tests/
│   └── test_arb_calculator.py         # Python unit tests (pytest)
├── .env.example                       # Environment variable template
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Quick Start

### 1 — Prerequisites

| Tool | Version | Install |
|---|---|---|
| Python | ≥ 3.11 | [python.org](https://www.python.org) |
| Foundry | latest | `curl -L https://foundry.paradigm.xyz \| bash` |
| Docker + Compose | latest | [docs.docker.com](https://docs.docker.com) |

### 2 — Configure environment

```bash
cp .env.example .env
# Edit .env — fill in your RPC URL, Gemini API key, and webhook URL
```

**Required keys:**

| Key | Description |
|---|---|
| `POLYGON_WS_RPC_URL` | Polygon WebSocket RPC (Alchemy / Infura) |
| `GEMINI_API_KEY` | Google Gemini API key |
| `DISCORD_WEBHOOK_URL` **or** `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Notification channel |

### 3 — Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4 — Install Foundry dependencies

```bash
cd contracts
forge install OpenZeppelin/openzeppelin-contracts
forge build
```

### 5 — Run the bot locally

```bash
cd bot
python main.py
```

### 6 — Run with Docker

```bash
docker compose up --build
```

---

## Testing

### Python unit tests

```bash
pytest tests/
```

### Foundry fork tests (requires live Polygon RPC)

```bash
cd contracts
forge test --fork-url $POLYGON_WS_RPC_URL -vv
```

Individual test:
```bash
forge test --fork-url $POLYGON_WS_RPC_URL --match-test testArbitrageSimulation -vv
```

---

## Pipeline Flow

1. **price_monitor** polls Uniswap V3 (`slot0`) and SushiSwap (`getReserves`) every 2 s for WETH/USDC, WMATIC/USDC, and WBTC/USDC.
2. **arb_calculator** computes net profit = gross spread − Aave fee (0.05%) − swap fees − slippage (0.5%) − gas cost.
3. If `net_profit > MIN_PROFIT_USD` ($5 default), **graph_client** fetches TVL + 24h volatility from The Graph.
4. **gemini_agent** calls Gemini to return a JSON risk score (0–10) and recommendation (`EXECUTE` / `MONITOR` / `SKIP`).
5. If Gemini does not recommend `SKIP`, **simulation** runs `forge test --fork-url` against Polygon mainnet fork.
6. If the simulation passes, **notifier** sends a Gemini-formatted alert to Discord or Telegram.

**No live execution occurs in the MVP.** The `requestFlashLoan` function on `ArbitrageExecutor.sol` is only callable from the contract owner and is never invoked by the Python bot in this phase.

---

## Key Addresses (Polygon Mainnet)

| Contract | Address |
|---|---|
| Aave V3 Pool | `0x794a61358D6845594F94dc1DB02A252b5b4814aD` |
| Uniswap V3 Factory | `0x1F98431c8aD98523631AE4a59f267346ea31F984` |
| Uniswap V3 Router | `0xE592427A0AEce92De3Edee1F18E0157C05861564` |
| SushiSwap Factory | `0xc35DADB65012eC5796536bD9864eD8773aBc74C4` |
| SushiSwap Router | `0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506` |

---

## Security Notes

- **Never commit `.env`** — it is in `.gitignore`.
- The `DEPLOYER_PRIVATE_KEY` is used only for Foundry deployment scripts. Do not fund this wallet on mainnet during MVP.
- Production deployments should use AWS KMS instead of raw private keys.
- The `ArbitrageExecutor` contract has an `onlyOwner` guard on `requestFlashLoan` and `withdraw`.

---

## Roadmap

| Phase | Status | Description |
|---|---|---|
| Phase 1 (MVP) | 🔨 In Progress | Detect + Simulate + Report (no live funds) |
| Phase 2 | ⏳ Planned | Live execution on Polygon mainnet with real funds |
| Phase 3 | ⏳ Planned | AWS KMS key management + multi-pair expansion |
| Phase 4 | ⏳ Planned | MEV protection (Flashbots / private mempool) |

---

## License

MIT


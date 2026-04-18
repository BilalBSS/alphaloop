# self-improving agentic trading system

Runs 24/7 on a VPS. Trades US stocks (Alpaca) and crypto. Evolves its own strategies nightly via a Karpathy-style autoresearch loop. Budget: under $25/month.

## Quickstart

```bash
# 1. install python 3.11+ deps
python -m venv venv
venv/Scripts/pip install -r requirements.txt  # windows; on linux: venv/bin/pip

# 2. copy env template and fill in keys
cp .env.example .env
# edit .env: ALPACA_*, GROQ_API_KEY, CEREBRAS_API_KEY, SEC_EDGAR_USER_AGENT, DATABASE_URL

# 3. run migrations (auto on first start)
python main.py  # starts the orchestrator in paper mode
```

Dashboard: http://localhost:8000 after `uvicorn src.dashboard.app:app` (or access via systemd on the VPS).

## What's in here

- **`src/agents/`** — analyst, strategy, risk, executor + orchestrator
- **`src/data/`** — ingestion (market, fundamentals, SEC, FRED, congressional, analyst, short, options, dark pool, crypto, sentiment)
- **`src/indicators/`** — technical indicators (trend, momentum, volatility, volume, structure, breadth, intermarket)
- **`src/analysis/`** — ratio, DCF, earnings signals, insider, AI summary, strategy decay
- **`src/quant/`** — Monte Carlo, particle filter, copulas, risk metrics, Brier calibration
- **`src/strategies/`** — config-driven strategies + backtest + walk-forward
- **`src/evolution/`** — nightly kill/mutate/backtest/promote loop
- **`src/knowledge/`** — wiki docs + embeddings + post-mortems (Phase 2 learning layer)
- **`src/brokers/`** — Alpaca + paper broker behind a unified interface
- **`src/dashboard/`** — FastAPI backend + React/Vite frontend
- **`configs/strategies/`** — 16 JSON strategy configs (loaded + mutated by evolution)
- **`configs/risk_limits.json`** — portfolio-wide risk gates (enforced)
- **`trading-wiki/`** — on-disk cache of wiki content
- **`tests/`** — 2223 tests across 79 files

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — living reference for how the system fits together
- [docs/PHASE4_CHANGES.md](docs/PHASE4_CHANGES.md) — running log of Phase 4 cleanup changes

## Deploy

Ubuntu VPS, ~$8/mo. Two systemd services: `quant-trading` (orchestrator) + `quant-dashboard` (FastAPI). Cloudflare tunnel exposes the dashboard. See `docs/ARCHITECTURE.md` for details.

## Run tests

```bash
venv/Scripts/python -m pytest tests/ -q
```

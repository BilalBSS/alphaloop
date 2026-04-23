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

## Deploy

Ubuntu VPS, ~$8/mo. Two systemd services: `quant-trading` (orchestrator) + `quant-dashboard` (FastAPI). Cloudflare tunnel exposes the dashboard. See `docs/ARCHITECTURE.md` for details.

## Run tests

```bash
venv/Scripts/python -m pytest tests/ -q
```

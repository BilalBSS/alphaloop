# / fastapi dashboard backend — separate process from trading bot
# / serves api endpoints + react static files
# / bind to localhost by default — access via ssh tunnel

from __future__ import annotations

import asyncio
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.data.db import close_db, init_db

logger = structlog.get_logger(__name__)

app = FastAPI(title="Quant Trading Dashboard", docs_url="/api/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_pool: asyncpg.Pool | None = None
_ws_clients: set[WebSocket] = set()

STATIC_DIR = Path(__file__).parent / "static"


@app.on_event("startup")
async def startup():
    global _pool
    _pool = await init_db()
    # / mount react build if it exists
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


@app.on_event("shutdown")
async def shutdown():
    await close_db()


async def _query(sql: str, *args) -> list[dict]:
    if _pool is None:
        return []
    async with _pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]


async def _query_one(sql: str, *args) -> dict | None:
    if _pool is None:
        return None
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None


# / api endpoints

@app.get("/api/portfolio")
async def get_portfolio():
    # / current portfolio value, P&L, positions count
    positions = await _query(
        """SELECT symbol, side, qty, entry_price, current_price
        FROM trade_log WHERE exit_price IS NULL"""
    )
    trades_today = await _query(
        """SELECT * FROM trade_log
        WHERE created_at >= CURRENT_DATE ORDER BY created_at DESC"""
    )
    return {
        "positions_count": len(positions),
        "positions": _serialize(positions),
        "trades_today": _serialize(trades_today),
    }


@app.get("/api/positions")
async def get_positions():
    rows = await _query(
        """SELECT tl.symbol, tl.side, tl.qty, tl.price as entry_price,
                tl.strategy_id, tl.created_at
        FROM trade_log tl
        WHERE tl.exit_price IS NULL
        ORDER BY tl.created_at DESC"""
    )
    return _serialize(rows)


@app.get("/api/trades")
async def get_trades(limit: int = 100, offset: int = 0, symbol: str | None = None):
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    if symbol:
        rows = await _query(
            """SELECT * FROM trade_log WHERE symbol = $1
            ORDER BY created_at DESC LIMIT $2 OFFSET $3""",
            symbol, limit, offset,
        )
    else:
        rows = await _query(
            """SELECT * FROM trade_log
            ORDER BY created_at DESC LIMIT $1 OFFSET $2""",
            limit, offset,
        )
    return _serialize(rows)


@app.get("/api/analysis/{symbol}")
async def get_analysis(symbol: str):
    # / full deep-dive: fundamentals, DCF, dual-llm, indicators, trades, sentiment
    score = await _query_one(
        """SELECT * FROM analysis_scores
        WHERE symbol = $1 ORDER BY date DESC LIMIT 1""",
        symbol,
    )
    signals = await _query(
        """SELECT * FROM trade_signals
        WHERE symbol = $1 ORDER BY created_at DESC LIMIT 20""",
        symbol,
    )
    trades = await _query(
        """SELECT * FROM trade_log
        WHERE symbol = $1 ORDER BY created_at DESC LIMIT 20""",
        symbol,
    )
    sentiment = await _query(
        """SELECT date, sentiment_score, sentiment_label, source
        FROM news_sentiment WHERE symbol = $1
        ORDER BY date DESC LIMIT 30""",
        symbol,
    )
    fundamentals = await _query_one(
        """SELECT * FROM fundamentals
        WHERE symbol = $1 ORDER BY date DESC LIMIT 1""",
        symbol,
    )
    dcf = await _query_one(
        """SELECT * FROM dcf_valuations
        WHERE symbol = $1 ORDER BY date DESC LIMIT 1""",
        symbol,
    )
    market = await _query(
        """SELECT date, close, volume FROM market_data
        WHERE symbol = $1 ORDER BY date DESC LIMIT 60""",
        symbol,
    )
    social = await _query(
        """SELECT date, source, bullish_pct, bearish_pct, volume, raw_score
        FROM social_sentiment WHERE symbol = $1
        ORDER BY date DESC LIMIT 30""",
        symbol,
    )
    insider = await _query(
        """SELECT filing_date, insider_name, insider_title, transaction_type,
                shares, price_per_share, total_value
        FROM insider_trades WHERE symbol = $1
        ORDER BY filing_date DESC LIMIT 20""",
        symbol,
    )
    evolution = await _query(
        """SELECT generation, action, strategy_id, reason, details, created_at
        FROM evolution_log
        WHERE strategy_id IN (
            SELECT DISTINCT strategy_id FROM trade_signals WHERE symbol = $1
        ) OR details::text LIKE '%' || $1 || '%'
        ORDER BY created_at DESC LIMIT 20""",
        symbol,
    )
    return {
        "score": _serialize_one(score),
        "signals": _serialize(signals),
        "trades": _serialize(trades),
        "sentiment": _serialize(sentiment),
        "social_sentiment": _serialize(social),
        "fundamentals": _serialize_one(fundamentals),
        "dcf": _serialize_one(dcf),
        "price_history": _serialize(market),
        "insider_trades": _serialize(insider),
        "evolution": _serialize(evolution),
    }


@app.get("/api/symbols")
async def get_symbols():
    # / list all tracked symbols with latest score
    rows = await _query(
        """SELECT DISTINCT ON (symbol) symbol, date, composite_score, regime,
            details->>'ai_consensus' as ai_consensus
        FROM analysis_scores ORDER BY symbol, date DESC"""
    )
    return _serialize(rows)


@app.get("/api/strategies")
async def get_strategies():
    rows = await _query(
        """SELECT * FROM strategy_scores
        ORDER BY composite_score DESC NULLS LAST"""
    )
    return _serialize(rows)


@app.get("/api/evolution")
async def get_evolution():
    rows = await _query(
        """SELECT * FROM evolution_log
        ORDER BY generation DESC, created_at DESC LIMIT 50"""
    )
    return _serialize(rows)


@app.get("/api/health")
async def get_health():
    # / system health: db connection, last backfill, last evolution
    db_ok = False
    try:
        await _query_one("SELECT 1 as ok")
        db_ok = True
    except Exception:
        pass

    last_trade = await _query_one(
        "SELECT created_at FROM trade_log ORDER BY created_at DESC LIMIT 1"
    )
    last_evolution = await _query_one(
        "SELECT created_at FROM evolution_log ORDER BY created_at DESC LIMIT 1"
    )
    last_analysis = await _query_one(
        "SELECT date FROM analysis_scores ORDER BY date DESC LIMIT 1"
    )

    # / storage estimate
    storage = await _query_one(
        """SELECT pg_database_size(current_database()) as size_bytes"""
    )

    return {
        "db_connected": db_ok,
        "storage_bytes": storage["size_bytes"] if storage else 0,
        "storage_mb": round(storage["size_bytes"] / 1024 / 1024, 1) if storage else 0,
        "last_trade": str(last_trade["created_at"]) if last_trade else None,
        "last_evolution": str(last_evolution["created_at"]) if last_evolution else None,
        "last_analysis": str(last_analysis["date"]) if last_analysis else None,
    }


@app.get("/api/signals")
async def get_signals(limit: int = 50):
    limit = max(1, min(limit, 500))
    rows = await _query(
        """SELECT * FROM trade_signals
        ORDER BY created_at DESC LIMIT $1""",
        limit,
    )
    return _serialize(rows)


@app.get("/api/synthesis")
async def get_synthesis():
    # / latest daily synthesis from 5PM reasoner
    row = await _query_one(
        "SELECT * FROM daily_synthesis ORDER BY date DESC LIMIT 1"
    )
    return _serialize_one(row)


@app.get("/api/synthesis/history")
async def get_synthesis_history(days: int = 7):
    days = max(1, min(days, 30))
    rows = await _query(
        """SELECT * FROM daily_synthesis
        ORDER BY date DESC LIMIT $1""",
        days,
    )
    return _serialize(rows)


@app.get("/api/strategy-evaluations")
async def get_strategy_evaluations(limit: int = 20):
    limit = max(1, min(limit, 100))
    rows = await _query(
        """SELECT * FROM strategy_evaluations
        ORDER BY created_at DESC LIMIT $1""",
        limit,
    )
    return _serialize(rows)


# / websocket for live updates

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _ws_clients.discard(ws)


async def broadcast(event_type: str, data: dict) -> None:
    # / push event to all connected websocket clients
    message = json.dumps({"type": event_type, "data": _serialize_one(data)})
    disconnected = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)
    _ws_clients -= disconnected


# / serialization helpers for decimal/date/datetime types

def _serialize(rows: list[dict]) -> list[dict]:
    return [_serialize_one(r) for r in rows]


def _serialize_one(row: dict | None) -> dict | None:
    if row is None:
        return None
    result = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            result[k] = v.isoformat()
        elif isinstance(v, (int, float, str, bool, type(None))):
            result[k] = v
        elif isinstance(v, (dict, list)):
            result[k] = v
        else:
            result[k] = str(v)
    return result


def run():
    import uvicorn
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()

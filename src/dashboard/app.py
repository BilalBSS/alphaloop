# / fastapi dashboard backend — separate process from trading bot
# / serves api endpoints + react static files
# / bind to localhost by default — access via ssh tunnel

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import asyncpg
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.data.db import close_db, init_db

logger = structlog.get_logger(__name__)

_pool: asyncpg.Pool | None = None
_ws_clients: set[WebSocket] = set()
_broker = None

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    _pool = await init_db()
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    yield
    await close_db()


app = FastAPI(title="Quant Trading Dashboard", docs_url="/api/docs", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_broker():
    # / lazy singleton — avoid re-instantiating on every request
    global _broker
    if _broker is None:
        from src.brokers.alpaca_broker import AlpacaBroker
        _broker = AlpacaBroker()
    return _broker


def _serialize_position(p) -> dict:
    # / consistent position dict for portfolio + positions endpoints
    return {
        "symbol": p.symbol,
        "side": p.side,
        "qty": p.qty,
        "market_value": p.market_value,
        "entry_price": p.avg_entry_price,
        "unrealized_pl": p.unrealized_pnl,
        "current_price": p.current_price,
    }


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
    # / pull live data from alpaca, fall back to trade_log
    try:
        broker = _get_broker()
        balance = await broker.get_account_balance()
        positions = await broker.get_positions()
        return {
            "equity": balance.equity,
            "cash": balance.cash,
            "buying_power": balance.buying_power,
            "positions_count": len(positions),
            "daily_pnl": sum(p.unrealized_pnl for p in positions),
            "positions": [_serialize_position(p) for p in positions],
            "trades_today": _serialize(await _query(
                """SELECT * FROM trade_log
                WHERE created_at >= CURRENT_DATE ORDER BY created_at DESC"""
            )),
        }
    except Exception as exc:
        logger.debug("portfolio_alpaca_fallback", error=str(exc))
        # / fallback to db
        positions = await _query(
            """SELECT symbol, side, qty, price, strategy_id, created_at
            FROM trade_log ORDER BY created_at DESC LIMIT 50"""
        )
        return {"positions_count": 0, "positions": _serialize(positions), "trades_today": []}


@app.get("/api/equity-history")
async def get_equity_history(period: str = "1D", timeframe: str = "5Min"):
    # / pull portfolio history from alpaca for equity curve
    from src.data.alpaca_client import alpaca_base_url, alpaca_headers, get_alpaca_client
    base = alpaca_base_url()
    headers = alpaca_headers()
    try:
        client = await get_alpaca_client()
        resp = await client.get(
            f"{base}/v2/account/portfolio/history",
            headers=headers,
            params={"period": period, "timeframe": timeframe, "intraday_reporting": "market_hours", "pnl_reset": "per_day"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        timestamps = data.get("timestamp", [])
        equity = data.get("equity", [])
        profit_loss = data.get("profit_loss", [])
        return {
            "timestamps": timestamps,
            "equity": equity,
            "profit_loss": profit_loss,
            "base_value": data.get("base_value", 100000),
        }
    except Exception as exc:
        logger.debug("equity_history_failed", error=str(exc))
        return {"timestamps": [], "equity": [], "profit_loss": [], "base_value": 100000}


@app.get("/api/strategy-positions")
async def get_strategy_positions(symbol: str | None = None):
    # / per-equity breakdown: which strategy owns what
    if symbol:
        rows = await _query(
            """SELECT strategy_id, symbol, qty, avg_entry_price, updated_at
            FROM strategy_positions WHERE symbol = $1
            ORDER BY strategy_id""",
            symbol,
        )
    else:
        rows = await _query(
            """SELECT strategy_id, symbol, qty, avg_entry_price, updated_at
            FROM strategy_positions ORDER BY symbol, strategy_id"""
        )
    return _serialize(rows)


@app.get("/api/positions")
async def get_positions():
    # / pull live positions from alpaca
    try:
        broker = _get_broker()
        positions = await broker.get_positions()
        return [_serialize_position(p) for p in positions]
    except Exception as exc:
        logger.debug("positions_alpaca_fallback", error=str(exc))
        rows = await _query(
            """SELECT tl.symbol, tl.side, tl.qty, tl.price as entry_price,
                    tl.strategy_id, tl.created_at
            FROM trade_log tl WHERE tl.exit_price IS NULL
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
    # / parallel fetch — all queries are independent
    (score, signals, trades, sentiment, fundamentals, dcf, market, social, insider, evolution) = await asyncio.gather(
        _query_one(
            """SELECT * FROM analysis_scores
            WHERE symbol = $1 ORDER BY date DESC LIMIT 1""",
            symbol,
        ),
        _query(
            """SELECT * FROM trade_signals
            WHERE symbol = $1 ORDER BY created_at DESC LIMIT 20""",
            symbol,
        ),
        _query(
            """SELECT * FROM trade_log
            WHERE symbol = $1 ORDER BY created_at DESC LIMIT 20""",
            symbol,
        ),
        _query(
            """SELECT date, sentiment_score, sentiment_label, source
            FROM news_sentiment WHERE symbol = $1
            ORDER BY date DESC LIMIT 30""",
            symbol,
        ),
        _query_one(
            """SELECT f.*,
                s.avg_fcf_margin as sector_fcf_margin_avg,
                s.avg_de as sector_de_avg,
                s.avg_rev_growth as sector_rev_growth_avg
            FROM fundamentals f
            LEFT JOIN LATERAL (
                SELECT AVG(fcf_margin) as avg_fcf_margin,
                       AVG(debt_to_equity) as avg_de,
                       AVG(revenue_growth_1y) as avg_rev_growth
                FROM fundamentals f2
                WHERE f2.sector = f.sector AND f2.date = f.date AND f2.symbol != f.symbol
            ) s ON true
            WHERE f.symbol = $1 ORDER BY f.date DESC LIMIT 1""",
            symbol,
        ),
        _query_one(
            """SELECT * FROM dcf_valuations
            WHERE symbol = $1 AND fair_value_median IS NOT NULL
            ORDER BY date DESC LIMIT 1""",
            symbol,
        ),
        _query(
            """SELECT date, close, volume FROM market_data
            WHERE symbol = $1 ORDER BY date DESC LIMIT 60""",
            symbol,
        ),
        _query(
            """SELECT date, source, bullish_pct, bearish_pct, volume, raw_score
            FROM social_sentiment WHERE symbol = $1
            ORDER BY date DESC LIMIT 30""",
            symbol,
        ),
        _query(
            """SELECT filing_date, insider_name, insider_title, transaction_type,
                    shares, price_per_share, total_value
            FROM insider_trades WHERE symbol = $1
            ORDER BY filing_date DESC LIMIT 20""",
            symbol,
        ),
        _query(
            """SELECT generation, action, strategy_id, reason, details, created_at
            FROM evolution_log
            WHERE strategy_id IN (
                SELECT DISTINCT strategy_id FROM trade_signals WHERE symbol = $1
            ) OR details::text LIKE '%' || $1 || '%'
            ORDER BY created_at DESC LIMIT 20""",
            symbol,
        ),
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
    # / list tracked symbols with latest score — filter to active universe only
    from src.data.symbols import FULL_UNIVERSE
    rows = await _query(
        """SELECT DISTINCT ON (symbol) symbol, date, composite_score, regime,
            details->>'ai_consensus' as ai_consensus
        FROM analysis_scores
        WHERE symbol = ANY($1)
        ORDER BY symbol, date DESC""",
        FULL_UNIVERSE,
    )
    return _serialize(rows)


@app.get("/api/strategies")
async def get_strategies():
    rows = await _query(
        """SELECT * FROM strategy_scores
        ORDER BY sharpe_ratio DESC NULLS LAST"""
    )
    # / fallback: compute basic metrics from trade_log when strategy_scores is empty
    if not rows:
        rows = await _query(
            """SELECT strategy_id,
                COUNT(*) as total_trades,
                COUNT(*) FILTER (WHERE pnl > 0) as wins,
                COUNT(*) FILTER (WHERE pnl < 0) as losses,
                COALESCE(ROUND(AVG(pnl)::numeric, 2), 0) as avg_pnl,
                COALESCE(ROUND(SUM(pnl)::numeric, 2), 0) as total_pnl,
                ROUND(COUNT(*) FILTER (WHERE pnl > 0)::numeric / NULLIF(COUNT(*), 0), 3) as win_rate,
                MAX(created_at) as last_trade_at
            FROM trade_log
            WHERE strategy_id IS NOT NULL
            GROUP BY strategy_id
            ORDER BY total_pnl DESC"""
        )
    # / enrich with name/status/description from config files
    result = _serialize(rows)
    _enrich_strategy_metadata(result)
    return result


def _enrich_strategy_metadata(rows: list[dict]) -> None:
    # / load strategy names and status from json configs on disk
    from pathlib import Path
    configs_dir = Path(__file__).parent.parent / "strategies" / ".." / ".." / "configs" / "strategies"
    configs_dir = configs_dir.resolve()
    for row in rows:
        sid = row.get("strategy_id")
        if not sid:
            continue
        config_path = (configs_dir / f"{sid}.json").resolve()
        if not config_path.is_relative_to(configs_dir) or not config_path.exists():
            continue
        try:
            cfg = json.loads(config_path.read_text())
            row.setdefault("name", cfg.get("name"))
            row.setdefault("status", cfg.get("metadata", {}).get("status"))
            row.setdefault("description", cfg.get("description"))
        except Exception as exc:
            logger.warning("strategy_config_read_failed", strategy_id=sid, error=str(exc))


@app.get("/api/evolution")
async def get_evolution():
    rows = await _query(
        """SELECT * FROM evolution_log
        ORDER BY generation DESC, created_at DESC LIMIT 50"""
    )
    return _serialize(rows)


@app.get("/api/health")
async def get_health():
    # / system health v2: db, cycles, storage, connections, events
    db_ok = False
    try:
        await _query_one("SELECT 1 as ok")
        db_ok = True
    except Exception:
        pass

    # / parallel fetch — all remaining queries are independent
    (
        last_trade, last_evolution, last_analysis, last_synthesis, last_eval,
        symbols_analyzed, last_llm, db_size, tables, conn_stats, active,
        recent_errors, source_stats,
    ) = await asyncio.gather(
        _query_one("SELECT created_at FROM trade_log ORDER BY created_at DESC LIMIT 1"),
        _query_one("SELECT created_at FROM evolution_log ORDER BY created_at DESC LIMIT 1"),
        _query_one(
            """SELECT timestamp FROM system_events
            WHERE source = 'analyst' ORDER BY timestamp DESC LIMIT 1"""
        ),
        _query_one("SELECT date FROM daily_synthesis ORDER BY date DESC LIMIT 1"),
        _query_one("SELECT created_at FROM strategy_evaluations ORDER BY created_at DESC LIMIT 1"),
        _query_one(
            """SELECT COUNT(DISTINCT symbol) as cnt FROM analysis_scores
            WHERE date >= CURRENT_DATE"""
        ),
        _query_one(
            """SELECT symbol, details->>'llm_analysis_groq' as groq,
                    details->>'llm_analysis_deepseek' as deepseek
            FROM analysis_scores WHERE date >= CURRENT_DATE
            ORDER BY date DESC LIMIT 1"""
        ),
        _query_one("SELECT pg_database_size(current_database()) as size_bytes"),
        _query(
            """SELECT relname as name,
                pg_total_relation_size(relid) as size_bytes,
                n_live_tup as rows
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC LIMIT 10"""
        ),
        _query_one(
            """SELECT numbackends, xact_commit, xact_rollback, blks_read, blks_hit
            FROM pg_stat_database WHERE datname = current_database()"""
        ),
        _query_one("SELECT COUNT(*) as cnt FROM pg_stat_activity WHERE state = 'active'"),
        _query(
            """SELECT timestamp, source, symbol, message
            FROM system_events WHERE level IN ('error', 'warning')
            ORDER BY timestamp DESC LIMIT 20"""
        ),
        _query(
            """SELECT source,
                COUNT(*) FILTER (WHERE level = 'error') as errors_24h,
                MAX(timestamp) FILTER (WHERE level = 'error') as last_error
            FROM system_events
            WHERE timestamp > NOW() - INTERVAL '24 hours'
            GROUP BY source"""
        ),
        return_exceptions=True,
    )

    # / handle errors from gathered results — use fallbacks matching original behavior
    if isinstance(last_trade, Exception):
        last_trade = None
    if isinstance(last_evolution, Exception):
        last_evolution = None
    if isinstance(last_analysis, Exception):
        last_analysis = None
    if isinstance(last_synthesis, Exception):
        last_synthesis = None
    if isinstance(last_eval, Exception):
        last_eval = None
    if isinstance(symbols_analyzed, Exception):
        symbols_analyzed = None
    if isinstance(last_llm, Exception):
        last_llm = None

    # / groq vs deepseek status
    groq_status = "unknown"
    if last_llm:
        groq_text = last_llm.get("groq") or ""
        # / fallback format starts with "SYMBOL —", llm format is a paragraph
        groq_status = "fallback" if " — " in groq_text[:30] else "active"
    deepseek_status = "active" if (last_llm and last_llm.get("deepseek")) else "pending"

    # / db size
    if isinstance(db_size, Exception) or not db_size:
        db_size_mb = None
    else:
        db_size_mb = round(db_size["size_bytes"] / 1024 / 1024, 1)

    # / per-table sizes + row counts (top 10)
    if isinstance(tables, Exception) or not tables:
        table_stats = []
    else:
        table_stats = [
            {"name": t["name"], "size_mb": round(t["size_bytes"] / 1024 / 1024, 2), "rows": t["rows"]}
            for t in tables
        ]

    # / connection stats from pg_stat_database
    if isinstance(conn_stats, Exception):
        conn_stats = None
        cache_ratio = 0
    elif conn_stats:
        hit = conn_stats["blks_hit"] or 0
        read = conn_stats["blks_read"] or 0
        cache_ratio = round(hit / (hit + read), 4) if (hit + read) > 0 else 0
    else:
        cache_ratio = 0

    # / active connections count
    if isinstance(active, Exception):
        active_conns = None
    else:
        active_conns = active["cnt"] if active else 0

    # / recent errors from system_events
    if isinstance(recent_errors, Exception):
        recent_errors = []

    # / per-source health status (errors in last 24h)
    if isinstance(source_stats, Exception) or not source_stats:
        sources = {}
    else:
        sources = {}
        for s in source_stats:
            sources[s["source"]] = {
                "status": "degraded" if s["errors_24h"] > 0 else "active",
                "last_error": str(s["last_error"]) if s["last_error"] else None,
                "errors_24h": s["errors_24h"],
            }

    # / ensure groq + deepseek always present in sources
    if "groq" not in sources:
        sources["groq"] = {"status": groq_status, "last_error": None, "errors_24h": 0}
    if "deepseek" not in sources:
        sources["deepseek"] = {"status": deepseek_status, "last_error": None, "errors_24h": 0}

    return {
        "db_connected": db_ok,
        "storage": {
            "db_size_mb": db_size_mb,
            "tables": table_stats,
        },
        "connections": {
            "active": active_conns,
            "commits": conn_stats["xact_commit"] if conn_stats else None,
            "rollbacks": conn_stats["xact_rollback"] if conn_stats else None,
            "cache_hit_ratio": cache_ratio,
        },
        "cycles": {
            "last_analysis": str(last_analysis["timestamp"]) if last_analysis else None,
            "last_strategy_eval": str(last_eval["created_at"]) if last_eval else None,
            "last_evolution": str(last_evolution["created_at"]) if last_evolution else None,
            "last_trade": str(last_trade["created_at"]) if last_trade else None,
            "last_synthesis": str(last_synthesis["date"]) if last_synthesis else None,
            "symbols_today": symbols_analyzed["cnt"] if symbols_analyzed else 0,
        },
        "sources": sources,
        "recent_errors": _serialize(recent_errors),
    }


@app.get("/api/insider/{symbol}")
async def get_insider(symbol: str):
    # / recent insider trades for symbol (last 90 days)
    rows = await _query(
        """SELECT * FROM insider_trades
        WHERE symbol = $1 AND filing_date > CURRENT_DATE - INTERVAL '90 days'
        ORDER BY filing_date DESC LIMIT 20""",
        symbol.upper(),
    )
    return _serialize(rows)


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


@app.get("/api/indicators/{symbol}")
async def get_indicators(symbol: str, limit: int = 60, timeframe: str = "1Day"):
    limit = max(1, min(limit, 250))
    rows = await _query(
        """SELECT date, rsi14, macd, macd_signal, macd_histogram,
        adx, sma20, sma50, bb_upper, bb_middle, bb_lower, atr, timeframe
        FROM computed_indicators
        WHERE symbol = $1 AND timeframe = $2 ORDER BY date DESC LIMIT $3""",
        symbol, timeframe, limit,
    )
    return _serialize(rows)


@app.get("/api/intraday/{symbol}")
async def get_intraday(symbol: str, days: int = 10, timeframe: str = "2Hour"):
    days = max(1, min(days, 60))
    rows = await _query(
        """SELECT timestamp, open, high, low, close, volume, vwap
        FROM market_data_intraday
        WHERE symbol = $1 AND timeframe = $2
            AND timestamp > NOW() - ($3 || ' days')::INTERVAL
        ORDER BY timestamp ASC""",
        symbol, timeframe, str(days),
    )
    return _serialize(rows)


@app.get("/api/ict-indicators/{symbol}")
async def get_ict_indicators(symbol: str):
    rows = await _query(
        """SELECT ict_data FROM computed_indicators
        WHERE symbol = $1 AND timeframe = '1Day' AND ict_data IS NOT NULL
        ORDER BY date DESC LIMIT 1""",
        symbol,
    )
    if rows and rows[0].get("ict_data"):
        data = rows[0]["ict_data"]
        if isinstance(data, dict):
            return data
        return json.loads(data) if isinstance(data, str) else {}
    return {"fvgs": [], "order_blocks": [], "structure_breaks": []}


@app.get("/api/quant-metrics/{symbol}")
async def get_quant_metrics(symbol: str):
    rows = await _query(
        """SELECT ss.* FROM strategy_scores ss
        WHERE ss.strategy_id IN (
            SELECT DISTINCT strategy_id FROM trade_signals WHERE symbol = $1
        )
        ORDER BY ss.sharpe_ratio DESC NULLS LAST""",
        symbol,
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

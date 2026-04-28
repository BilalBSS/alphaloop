# / fastapi dashboard backend — separate process from trading bot
# / serves api endpoints + react static files
# / bind to localhost by default — access via ssh tunnel

from __future__ import annotations

import asyncio
import hmac
import json
import os
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.dashboard import alerts as alerts_mod
from src.dashboard import chart_state as chart_state_mod
from src.dashboard import compare as compare_mod
from src.dashboard import drawings as drawings_mod
from src.dashboard import indicator_registry
from src.dashboard import marker_aggregator as marker_agg_mod
from src.dashboard import replay as replay_mod
from src.dashboard import volume_profile as volume_profile_mod
from src.dashboard.state import STATE, get_state
from src.data.db import close_db, init_db

logger = structlog.get_logger(__name__)

# / load env config once at import (must precede middleware setup below)
STATE.load_config_from_env()

# / module-level alias for external imports (executor, orchestrator, evolution).
# / set is mutated in place, so consumers always see live state.
_ws_clients = STATE.ws_clients

STATIC_DIR = Path(__file__).parent / "static"
STRATEGY_CONFIGS_DIR = (Path(__file__).parent.parent.parent / "configs" / "strategies").resolve()


@asynccontextmanager
async def lifespan(app: FastAPI):
    STATE.pool = await init_db()
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    yield
    await STATE.aclose()
    await close_db()


app = FastAPI(title="Quant Trading Dashboard", docs_url="/api/docs", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=STATE.cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=["*"],
)


# / FastAPI APIRoute sets methods={'GET'} without adding HEAD, so HEAD to /api/ returns 404
# / gate rewrite to /api/ paths only — static assets already handle HEAD natively in StaticFiles,
# / mutating scope["method"] there would force full file reads on every HEAD ping
@app.middleware("http")
async def _head_fallback(request, call_next):
    if request.method == "HEAD" and request.url.path.startswith("/api/"):
        request.scope["method"] = "GET"
    return await call_next(request)


# / cache-busting: vite fingerprints all js/css bundles so they're immutable, but
# / /index.html references them by hash — serving a stale index means the browser
# / asks for a bundle that no longer exists on disk. send no-cache for html only.
@app.middleware("http")
async def _no_cache_html(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(".html") or path.endswith("/index.html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def _get_broker():
    # / lazy singleton — defer instantiation and broker import
    return STATE.get_broker()


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
    if STATE.pool is None:
        return []
    async with STATE.pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]


async def _query_one(sql: str, *args) -> dict | None:
    if STATE.pool is None:
        return None
    async with STATE.pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None


def _check_admin_token(supplied: str | None) -> bool:
    if not STATE.admin_token:
        return False
    if not supplied:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), STATE.admin_token.encode("utf-8"))


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return ""


async def require_admin_token(request: Request) -> None:
    if not STATE.admin_token:
        return
    supplied = _extract_bearer(request)
    if not _check_admin_token(supplied):
        raise HTTPException(status_code=401, detail="unauthorized")


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    logger.error(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        error_type=type(exc).__name__,
        traceback=traceback.format_exc(),
    )
    return JSONResponse(status_code=500, content={"detail": "internal error"})


# / api endpoints

@app.get("/api/portfolio")
async def get_portfolio():
    # / pull live data from alpaca, fall back to trade_log
    try:
        broker = _get_broker()
        balance, positions = await asyncio.gather(
            broker.get_account_balance(),
            broker.get_positions(),
        )
        trades_today = _serialize(await _query(
            """SELECT * FROM trade_log
            WHERE created_at >= CURRENT_DATE ORDER BY created_at DESC"""
        ))
        # / daily pnl = open-position unrealized + today's realized fills
        # / (prior formula only counted unrealized and read 0 on a flat book)
        unrealized = sum(p.unrealized_pnl for p in positions)
        realized = 0.0
        for t in trades_today:
            pnl = t.get("pnl")
            if pnl is None:
                continue
            try:
                realized += float(pnl)
            except (TypeError, ValueError):
                continue
        return {
            "equity": balance.equity,
            "cash": balance.cash,
            "buying_power": balance.buying_power,
            "positions_count": len(positions),
            "daily_pnl": unrealized + realized,
            "positions": [_serialize_position(p) for p in positions],
            "trades_today": trades_today,
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


@app.get("/api/trades/{trade_id}/detail")
async def get_trade_detail(trade_id: int):
    # / expanded trade view: trade_log row + originating signal + approved_trade + analysis snapshot
    trade = await _query_one(
        "SELECT * FROM trade_log WHERE id = $1", trade_id,
    )
    if not trade:
        return JSONResponse({"error": "trade not found"}, status_code=404)
    signal = None
    approved = None
    if trade.get("trade_id") is not None:
        approved = await _query_one(
            "SELECT * FROM approved_trades WHERE id = $1", trade["trade_id"],
        )
        if approved and approved.get("signal_id") is not None:
            signal = await _query_one(
                "SELECT * FROM trade_signals WHERE id = $1", approved["signal_id"],
            )
    analysis = await _query_one(
        """SELECT date, composite_score, fundamental_score, technical_score,
                regime, regime_confidence, details
        FROM analysis_scores
        WHERE symbol = $1 AND date <= $2::date
        ORDER BY date DESC LIMIT 1""",
        trade["symbol"], trade.get("created_at"),
    )
    return {
        "trade": _serialize_one(trade),
        "signal": _serialize_one(signal) if signal else None,
        "approved": _serialize_one(approved) if approved else None,
        "analysis": _serialize_one(analysis) if analysis else None,
    }


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


@app.get("/api/crypto-fundamentals/{symbol}")
async def get_crypto_fundamentals(symbol: str):
    # / crypto alt-data aggregate (nvt, funding, tvl, active addrs,
    # / exchange flows, hash rate, dex volume, stablecoin supply ratio).
    # / cache-first via crypto_fundamentals table, falls back to live fetchers.
    # / single source failures return null for that field — never 500s the request.
    from src.data.crypto_fundamentals import get_fundamentals
    from src.data.symbols import is_crypto
    sym = symbol.upper()
    if not is_crypto(sym):
        return JSONResponse({"error": "not a crypto symbol"}, status_code=400)
    try:
        return await get_fundamentals(STATE.pool, sym)
    except Exception as exc:
        # / last-resort safety net — shape stays consistent so the widget can render
        logger.warning("crypto_fundamentals_endpoint_failed", symbol=sym, error=str(exc)[:200])
        from datetime import datetime, timezone
        return {
            "nvt_ratio": None,
            "funding_rate": None,
            "active_addresses": None,
            "exchange_inflow_usd": None,
            "hash_rate": None,
            "tvl_usd": None,
            "dex_volume_24h": None,
            "stablecoin_supply_ratio": None,
            "sources": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


@app.get("/api/phase5-metrics")
async def get_phase5_metrics():
    # / flywheel health metrics, db-sourced
    from src.agents.analyst_agent import get_coverage_pct
    from src.data.loop_registry import fetch_service_state
    from src.agents.phase5_metrics import compute_phase5_metrics
    from src.data.symbols import FULL_UNIVERSE
    try:
        metrics = await compute_phase5_metrics(STATE.pool)
        row = await fetch_service_state(STATE.pool, "kronos_hf_load")
        if row:
            kronos_payload = {
                "hf_loaded": row.get("last_status") == "success",
                "load_attempted": row.get("last_fire_ts") is not None,
                "fallback_reason": row.get("last_error"),
                "last_status": row.get("last_status"),
                "last_update": row.get("updated_at").isoformat() if row.get("updated_at") else None,
            }
        else:
            # / orchestrator hasn't written yet — fall back to module read so a fresh
            # / boot doesn't show an empty field on the first dashboard request.
            from src.quant.kronos_signal import get_load_status as kronos_status
            kronos_payload = kronos_status()

        # / analyst freshness. 60-min window matches the intended full-universe
        # / refresh cadence (3 batches x 20-min interval). <0.8 = trading on stale signals.
        coverage_60m = await get_coverage_pct(STATE.pool, list(FULL_UNIVERSE), window_s=3600.0)
        metrics_dict = metrics.as_dict()
        metrics_dict["analyst_coverage_pct_60m"] = coverage_60m
        success = metrics.success_criteria()
        success["analyst_coverage_80pct_60m"] = coverage_60m >= 0.80

        return {
            "metrics": metrics_dict,
            "success_criteria": success,
            "all_pass": metrics.all_pass() and success["analyst_coverage_80pct_60m"],
            "kronos": kronos_payload,
        }
    except Exception as exc:
        logger.warning(
            "phase5_metrics_endpoint_failed",
            error=str(exc)[:200],
            traceback=traceback.format_exc(),
        )
        return JSONResponse(
            {"error": "phase5 metrics unavailable", "detail": "internal error"},
            status_code=500,
        )


@app.get("/api/symbols")
async def get_symbols():
    # / list ALL universe symbols with latest score if any — bug 5a: un-analyzed symbols
    # / were silently missing from the response, dashboard showed 51 of 54 on a cold pool
    from src.data.symbols import FULL_UNIVERSE
    scored = await _query(
        """SELECT DISTINCT ON (symbol) symbol, date, composite_score,
            fundamental_score, technical_score, regime,
            details->>'ai_consensus' as ai_consensus
        FROM analysis_scores
        WHERE symbol = ANY($1)
        ORDER BY symbol, date DESC""",
        FULL_UNIVERSE,
    )
    by_symbol = {row["symbol"]: row for row in scored}
    full = []
    for sym in FULL_UNIVERSE:
        row = by_symbol.get(sym)
        if row is not None:
            full.append(row)
        else:
            full.append({
                "symbol": sym, "date": None, "composite_score": None,
                "fundamental_score": None, "technical_score": None,
                "regime": None, "ai_consensus": None,
            })
    return _serialize(full)


@app.get("/api/strategies")
async def get_strategies():
    # / build baseline from config files — all strategies appear even if never traded
    strategies_by_id = {}
    for config_path in sorted(STRATEGY_CONFIGS_DIR.glob("*.json")):
        try:
            cfg = json.loads(config_path.read_text())
            sid = cfg.get("id", config_path.stem)
            entry_signals = cfg.get("entry_conditions", {}).get("signals", [])
            exit_conds = cfg.get("exit_conditions", {})
            # / seed sharpe/mdd/brier/win_rate from backtest metadata so strategies
            # / that haven't been live-scored yet still show their offline numbers.
            # / strategy_scores rows (when present) override these below — so live
            # / metrics always win. metrics_source tells the UI which we rendered.
            meta = cfg.get("metadata", {}) or {}
            backtest_sharpe = meta.get("backtest_sharpe")
            backtest_mdd = meta.get("backtest_max_drawdown")
            backtest_win_rate = meta.get("backtest_win_rate")
            backtest_brier = meta.get("brier_score")
            backtest_trade_count = meta.get("trade_count")
            strategies_by_id[sid] = {
                "strategy_id": sid,
                "name": cfg.get("name"),
                "status": meta.get("status"),
                "description": cfg.get("description"),
                "universe": cfg.get("universe"),
                "asset_class": cfg.get("asset_class"),
                "entry_conditions_count": len(entry_signals),
                "exit_conditions_count": len(exit_conds),
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0,
                "avg_pnl": 0,
                "win_rate": backtest_win_rate,
                "sharpe_ratio": backtest_sharpe,
                "max_drawdown": backtest_mdd,
                "brier_score": backtest_brier,
                "last_trade_at": None,
                "backtest_sharpe": backtest_sharpe,
                "backtest_max_drawdown": backtest_mdd,
                "backtest_win_rate": backtest_win_rate,
                "backtest_brier": backtest_brier,
                "backtest_trade_count": backtest_trade_count,
                "metrics_source": "backtest" if backtest_sharpe is not None else None,
            }
        except Exception as exc:
            logger.warning("strategy_config_read_failed", path=str(config_path), error=str(exc))

    # / overlay strategy_scores where available (most recent per strategy)
    score_rows = await _query(
        """SELECT DISTINCT ON (strategy_id) *
        FROM strategy_scores
        ORDER BY strategy_id, created_at DESC"""
    )
    for row in score_rows:
        sid = row.get("strategy_id")
        if sid and sid in strategies_by_id:
            strategies_by_id[sid].update({k: v for k, v in dict(row).items() if k != "strategy_id"})
            strategies_by_id[sid]["metrics_source"] = "live"
        elif sid:
            strategies_by_id[sid] = dict(row)
            strategies_by_id[sid]["metrics_source"] = "live"

    # / closed trades = sells with non-null pnl
    # / win_rate null until first closed trade
    trade_rows = await _query(
        """SELECT strategy_id,
            COUNT(*) FILTER (WHERE side = 'sell' AND pnl IS NOT NULL) as total_trades,
            COUNT(*) as fills_count,
            COUNT(*) FILTER (WHERE side = 'buy') as opens_count,
            COUNT(*) FILTER (WHERE side = 'sell' AND pnl > 0) as wins,
            COUNT(*) FILTER (WHERE side = 'sell' AND pnl < 0) as losses,
            COALESCE(ROUND(AVG(pnl) FILTER (WHERE side = 'sell' AND pnl IS NOT NULL)::numeric, 2), 0) as avg_pnl,
            COALESCE(ROUND(SUM(pnl) FILTER (WHERE side = 'sell' AND pnl IS NOT NULL)::numeric, 2), 0) as total_pnl,
            CASE
                WHEN COUNT(*) FILTER (WHERE side = 'sell' AND pnl IS NOT NULL) = 0 THEN NULL
                ELSE ROUND(
                    COUNT(*) FILTER (WHERE side = 'sell' AND pnl > 0)::numeric
                    / COUNT(*) FILTER (WHERE side = 'sell' AND pnl IS NOT NULL),
                    3
                )
            END as win_rate,
            MAX(created_at) as last_trade_at
        FROM trade_log
        WHERE strategy_id IS NOT NULL
        GROUP BY strategy_id"""
    )
    for row in trade_rows:
        sid = row.get("strategy_id")
        if sid and sid in strategies_by_id:
            strategies_by_id[sid].update({k: v for k, v in dict(row).items() if k != "strategy_id"})
        elif sid:
            strategies_by_id[sid] = dict(row)

    # / compute unrealized pnl from open strategy positions
    try:
        broker = _get_broker()
        alpaca_positions, sp_rows = await asyncio.gather(
            broker.get_positions(),
            _query(
                """SELECT strategy_id, symbol, qty, avg_entry_price
                FROM strategy_positions WHERE qty > 0"""
            ),
        )
        price_map = {p.symbol: p.current_price for p in alpaca_positions}
        unrealized_by_strategy: dict[str, float] = {}
        for sp in sp_rows:
            sid = sp.get("strategy_id")
            sym = sp.get("symbol")
            qty = float(sp.get("qty") or 0)
            entry = float(sp.get("avg_entry_price") or 0)
            price = price_map.get(sym, entry)
            unrealized_by_strategy[sid] = unrealized_by_strategy.get(sid, 0) + (price - entry) * qty

        for sid, upnl in unrealized_by_strategy.items():
            if sid in strategies_by_id:
                strategies_by_id[sid]["unrealized_pnl"] = round(upnl, 2)
    except Exception as exc:
        logger.debug("strategy_unrealized_pnl_failed", error=str(exc))

    # / sort by strategy_id natural order. sorting by pnl pushed strategies with
    # / negative unrealized to the bottom regardless of numeric id, which made
    # / strategy_011 appear last when it had the only open position — user read
    # / this as "no strat 11 box". numeric order is the readable default;
    # / pnl ranking can be a ui toggle later.
    def _natural_key(s):
        sid = s.get("strategy_id") or ""
        parts = sid.split("_")
        if len(parts) == 2 and parts[1].isdigit():
            return (parts[0], int(parts[1]))
        return (sid, 0)
    result = sorted(strategies_by_id.values(), key=_natural_key)
    return _serialize(result)


@app.get("/api/evolution")
async def get_evolution():
    rows = await _query(
        """SELECT * FROM evolution_log
        ORDER BY generation DESC, created_at DESC LIMIT 50"""
    )
    return _serialize(rows)


@app.get("/api/evolution/mutations")
async def get_evolution_mutations(limit: int = 100):
    # / wiki-guided A/B feed: recent evolution_mutations with wiki_guided flag + survival outcome
    limit = max(1, min(int(limit), 500))
    if STATE.pool is None:
        return {"mutations": [], "wiki_guided_count": 0, "random_count": 0, "wiki_win_rate": None, "random_win_rate": None}
    rows = await _query(
        """SELECT id, generation, parent_strategy_id, mutant_strategy_id,
                wiki_guided, wiki_context_tokens, parent_sharpe, mutant_sharpe,
                sharpe_delta, survived, created_at
        FROM evolution_mutations
        ORDER BY created_at DESC LIMIT $1""",
        limit,
    )
    mutations = _serialize(rows)
    wiki_rows = [m for m in mutations if m.get("wiki_guided")]
    rand_rows = [m for m in mutations if not m.get("wiki_guided")]
    wiki_survived = [m for m in wiki_rows if m.get("survived") is True]
    rand_survived = [m for m in rand_rows if m.get("survived") is True]
    wiki_win = (len(wiki_survived) / len(wiki_rows)) if wiki_rows else None
    rand_win = (len(rand_survived) / len(rand_rows)) if rand_rows else None
    return {
        "mutations": mutations,
        "wiki_guided_count": len(wiki_rows),
        "random_count": len(rand_rows),
        "wiki_win_rate": round(wiki_win, 3) if wiki_win is not None else None,
        "random_win_rate": round(rand_win, 3) if rand_win is not None else None,
    }


# / knowledge base endpoints
# / wiki_documents rows for sidebar browsing, raw markdown for content pane,
# / plus dedicated post_mortems + regime_shifts feeds for their own panels

_VALID_WIKI_CATEGORIES = {
    "regimes", "post-mortems", "strategies", "evolution", "symbols", "meta", "archive",
}


@app.get("/api/wiki/documents")
async def get_wiki_documents(
    category: str | None = None,
    symbol: str | None = None,
    strategy_id: str | None = None,
    limit: int = 200,
):
    # / list wiki_documents with optional filters; sidebar uses this to build the tree
    limit = max(1, min(int(limit), 500))
    clauses: list[str] = []
    params: list = []
    if category:
        if category not in _VALID_WIKI_CATEGORIES:
            return JSONResponse({"error": "invalid category"}, status_code=400)
        params.append(category)
        clauses.append(f"category = ${len(params)}")
    if symbol:
        params.append(symbol.upper())
        clauses.append(f"${len(params)} = ANY(symbols)")
    if strategy_id:
        params.append(strategy_id)
        clauses.append(f"${len(params)} = ANY(strategy_ids)")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    sql = (
        f"SELECT id, path, category, title, symbols, strategy_ids, "
        f"word_count, confidence, created_at, updated_at "
        f"FROM wiki_documents {where} "
        f"ORDER BY updated_at DESC LIMIT ${len(params)}"
    )
    rows = await _query(sql, *params)
    return _serialize(rows)


@app.get("/api/wiki/document")
async def get_wiki_document(path: str):
    # / return raw markdown for a given wiki path; security: must be in wiki_documents table
    if not path or ".." in path or path.startswith("/") or "\\" in path or "\x00" in path:
        return JSONResponse({"error": "invalid path"}, status_code=400)
    row = await _query_one(
        "SELECT path, category, title FROM wiki_documents WHERE path = $1", path,
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        from src.knowledge.wiki_writer import WikiWriter, get_wiki_root
        root = get_wiki_root().resolve()
        candidate = (root / path).resolve()
        if not str(candidate).startswith(str(root)):
            logger.warning("wiki_read_path_escape_blocked", path=path)
            return JSONResponse({"error": "invalid path"}, status_code=400)
        writer = WikiWriter(pool=STATE.pool)
        content = await writer.read_document(path)
    except Exception as exc:
        logger.warning("wiki_read_failed", path=path, error=str(exc)[:120])
        return JSONResponse({"error": "read failed"}, status_code=500)
    if content is None:
        return JSONResponse({"error": "file missing"}, status_code=404)
    return {
        "path": row["path"],
        "category": row["category"],
        "title": row["title"],
        "content": content,
    }


@app.get("/api/post-mortems")
async def get_post_mortems(strategy_id: str | None = None, limit: int = 50):
    # / recent post-mortems ordered newest first; optional strategy filter
    limit = max(1, min(int(limit), 200))
    if strategy_id:
        sql = (
            "SELECT id, strategy_id, symbol, trigger_type, pnl, expected_pnl, "
            "deviation_sigma, details, wiki_path, created_at FROM post_mortems "
            "WHERE strategy_id = $1 ORDER BY created_at DESC LIMIT $2"
        )
        rows = await _query(sql, strategy_id, limit)
    else:
        sql = (
            "SELECT id, strategy_id, symbol, trigger_type, pnl, expected_pnl, "
            "deviation_sigma, details, wiki_path, created_at FROM post_mortems "
            "ORDER BY created_at DESC LIMIT $1"
        )
        rows = await _query(sql, limit)
    return _serialize(rows)


@app.get("/api/regime-shifts")
async def get_regime_shifts(market: str | None = None, limit: int = 50):
    # / recent regime transitions; optional market (equity|crypto) filter
    limit = max(1, min(int(limit), 200))
    if market:
        if market not in ("equity", "crypto"):
            return JSONResponse({"error": "invalid market"}, status_code=400)
        sql = (
            "SELECT id, old_regime, new_regime, market, confidence, wiki_path, detected_at "
            "FROM regime_shifts WHERE market = $1 ORDER BY detected_at DESC LIMIT $2"
        )
        rows = await _query(sql, market, limit)
    else:
        sql = (
            "SELECT id, old_regime, new_regime, market, confidence, wiki_path, detected_at "
            "FROM regime_shifts ORDER BY detected_at DESC LIMIT $1"
        )
        rows = await _query(sql, limit)
    return _serialize(rows)


async def _health_db_ping() -> bool:
    try:
        await _query_one("SELECT 1 as ok")
        return True
    except Exception:
        return False


async def _health_gather_queries() -> dict:
    # / one parallel fetch of every independent health query
    keys = [
        "last_trade", "last_evolution", "last_synthesis", "last_eval",
        "symbols_analyzed", "last_llm", "db_size", "tables",
        "conn_stats", "active", "recent_errors", "source_stats",
    ]
    results = await asyncio.gather(
        _query_one("SELECT created_at FROM trade_log ORDER BY created_at DESC LIMIT 1"),
        _query_one("SELECT created_at FROM evolution_log ORDER BY created_at DESC LIMIT 1"),
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
    return {k: (None if isinstance(v, Exception) else v) for k, v in zip(keys, results, strict=True)}


def _health_storage(db_size, tables) -> dict:
    db_size_mb = round(db_size["size_bytes"] / 1024 / 1024, 1) if db_size else None
    table_stats = [
        {"name": t["name"], "size_mb": round(t["size_bytes"] / 1024 / 1024, 2), "rows": t["rows"]}
        for t in (tables or [])
    ]
    return {"db_size_mb": db_size_mb, "tables": table_stats}


def _health_connections(conn_stats, active) -> dict:
    if conn_stats:
        hit = conn_stats["blks_hit"] or 0
        read = conn_stats["blks_read"] or 0
        cache_ratio = round(hit / (hit + read), 4) if (hit + read) > 0 else 0
    else:
        cache_ratio = 0
    return {
        "active": active["cnt"] if active else 0,
        "commits": conn_stats["xact_commit"] if conn_stats else None,
        "rollbacks": conn_stats["xact_rollback"] if conn_stats else None,
        "cache_hit_ratio": cache_ratio,
    }


def _health_llm_statuses(last_llm) -> tuple[str, str]:
    # / (groq_status, deepseek_status)
    groq_status = "unknown"
    if last_llm:
        groq_text = last_llm.get("groq") or ""
        groq_status = "fallback" if " — " in groq_text[:30] else "active"
    deepseek_status = "active" if (last_llm and last_llm.get("deepseek")) else "pending"
    return groq_status, deepseek_status


def _health_sources(source_stats, groq_status: str, deepseek_status: str) -> dict:
    sources: dict = {}
    for s in source_stats or []:
        sources[s["source"]] = {
            "status": "degraded" if s["errors_24h"] > 0 else "active",
            "last_error": str(s["last_error"]) if s["last_error"] else None,
            "errors_24h": s["errors_24h"],
        }
    sources.setdefault("groq", {"status": groq_status, "last_error": None, "errors_24h": 0})
    sources.setdefault("deepseek", {"status": deepseek_status, "last_error": None, "errors_24h": 0})
    sources.setdefault("cerebras", {"status": "pending", "last_error": None, "errors_24h": 0})
    # / baseline orchestrator loops — pending until first cycle event
    for loop_name in (
        "intraday_backfill", "daily_bar_backfill", "price_refresh",
        "fundamentals_backfill", "insider_backfill", "regime_backfill",
        "alert", "alternative_data", "macro_backfill",
    ):
        sources.setdefault(loop_name, {"status": "pending", "last_error": None, "errors_24h": 0})
    return sources


async def _health_last_analysis_ts() -> str | None:
    # / canonical analyst timestamp: loop_registry.last_fire_ts for "analyst"
    try:
        from src.data.loop_registry import describe_loops
        loops_rows = await describe_loops(STATE.pool)
        analyst_row = next((loop for loop in loops_rows if loop.get("name") == "analyst"), None)
        if analyst_row and analyst_row.get("last_fire_ts"):
            lft = analyst_row["last_fire_ts"]
            return lft.isoformat() if hasattr(lft, "isoformat") else str(lft)
    except Exception:
        return None
    return None


@app.get("/api/health")
async def get_health():
    # / system health: db, cycles, storage, connections, events
    db_ok = await _health_db_ping()
    q = await _health_gather_queries()
    groq_status, deepseek_status = _health_llm_statuses(q["last_llm"])
    return {
        "db_connected": db_ok,
        "storage": _health_storage(q["db_size"], q["tables"]),
        "connections": _health_connections(q["conn_stats"], q["active"]),
        "cycles": {
            "last_analysis": await _health_last_analysis_ts(),
            "last_strategy_eval": str(q["last_eval"]["created_at"]) if q["last_eval"] else None,
            "last_evolution": str(q["last_evolution"]["created_at"]) if q["last_evolution"] else None,
            "last_trade": str(q["last_trade"]["created_at"]) if q["last_trade"] else None,
            "last_synthesis": str(q["last_synthesis"]["date"]) if q["last_synthesis"] else None,
            "symbols_today": q["symbols_analyzed"]["cnt"] if q["symbols_analyzed"] else 0,
        },
        "sources": _health_sources(q["source_stats"], groq_status, deepseek_status),
        "recent_errors": _serialize(q["recent_errors"] or []),
    }


@app.get("/api/insider/{symbol}")
async def get_insider(symbol: str):
    # / recent insider trades for symbol (last 90 days) + signed strength from latest analysis
    # / signed_strength lives in analysis_scores.details, not in insider_trades table
    sym = symbol.upper()
    rows = await _query(
        """SELECT * FROM insider_trades
        WHERE symbol = $1 AND filing_date > CURRENT_DATE - INTERVAL '90 days'
        ORDER BY filing_date DESC LIMIT 20""",
        sym,
    )
    latest_score = await _query_one(
        """SELECT details->>'insider_signed_strength' AS signed_strength,
                  details->>'insider_score_100' AS score_100
        FROM analysis_scores
        WHERE symbol = $1
        ORDER BY date DESC LIMIT 1""",
        sym,
    )
    signed = None
    score_100 = None
    if latest_score:
        try:
            raw_signed = latest_score.get("signed_strength")
            signed = float(raw_signed) if raw_signed is not None else None
        except (TypeError, ValueError):
            signed = None
        try:
            raw_score = latest_score.get("score_100")
            score_100 = float(raw_score) if raw_score is not None else None
        except (TypeError, ValueError):
            score_100 = None
    signal = "neutral"
    if signed is not None:
        if signed > 10:
            signal = "bullish"
        elif signed < -10:
            signal = "bearish"
    return {
        "trades": _serialize(rows),
        "signed_strength": signed,
        "score_100": score_100,
        "signal": signal,
    }


@app.get("/api/synthesis")
async def get_synthesis():
    # / latest daily synthesis from 5PM reasoner
    row = await _query_one(
        "SELECT * FROM daily_synthesis ORDER BY date DESC LIMIT 1"
    )
    return _serialize_one(row)


@app.get("/api/indicators/{symbol}")
async def get_indicators(symbol: str, limit: int = 60, timeframe: str = "1Day"):
    limit = max(1, min(limit, 250))
    rows = await _query(
        """SELECT date, rsi14, macd, macd_signal, macd_histogram,
        adx, sma20, sma50, bb_upper, bb_middle, bb_lower, atr, hurst, timeframe
        FROM computed_indicators
        WHERE symbol = $1 AND timeframe = $2 ORDER BY date DESC LIMIT $3""",
        symbol, timeframe, limit,
    )
    return _serialize(rows)


# / intraday cache keys + thin wrappers around STATE.intraday_cache (TTL=30s, max=256)
def _intraday_cache_key(symbol: str, timeframe: str, days: int, ids: tuple[str, ...]) -> tuple:
    return (symbol, timeframe, days, ids)


def _intraday_cache_get(key: tuple) -> object | None:
    return STATE.intraday_cache.get(key)


def _intraday_cache_put(key: tuple, payload: object) -> None:
    STATE.intraday_cache.put(key, payload)


def _intraday_cache_clear() -> None:
    STATE.intraday_cache.clear()


@app.get("/api/intraday/{symbol}")
async def get_intraday(symbol: str, days: int = 10, timeframe: str = "1Hour", indicators: str = ""):
    days = max(1, min(days, 60))

    # / parse requested ids up front so cache key includes them
    ids_sorted: tuple[str, ...] = tuple(sorted(i.strip() for i in indicators.split(",") if i.strip()))
    cache_key = _intraday_cache_key(symbol, timeframe, days, ids_sorted)
    cached = _intraday_cache_get(cache_key)
    if cached is not None:
        return cached

    # / skip caching entirely when the db is unreachable so an empty response does not
    # / mask real data for 30s once the pool comes back
    pool_ready = STATE.pool is not None

    rows = await _query(
        """SELECT timestamp, open, high, low, close, volume, vwap
        FROM market_data_intraday
        WHERE symbol = $1 AND timeframe = $2
            AND timestamp > NOW() - ($3 || ' days')::INTERVAL
        ORDER BY timestamp ASC""",
        symbol, timeframe, str(days),
    )
    # / empty indicators -> keep legacy shape for backwards compat
    if not ids_sorted:
        payload = _serialize(rows)
        if pool_ready and rows:
            _intraday_cache_put(cache_key, payload)
        return payload

    if not rows:
        payload = {
            "bars": {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []},
            "indicators": {},
            "meta": {"symbol": symbol, "timeframe": timeframe, "bar_count": 0},
        }
        if pool_ready:
            _intraday_cache_put(cache_key, payload)
        return payload

    # / build compact ohlcv arrays and a dataframe for indicator compute
    import pandas as pd  # / local import to avoid touching top-level imports

    t_list: list[str] = []
    o_list: list[float] = []
    h_list: list[float] = []
    l_list: list[float] = []
    c_list: list[float] = []
    v_list: list[float] = []
    for r in rows:
        ts = r.get("timestamp")
        t_list.append(ts.isoformat() if hasattr(ts, "isoformat") else str(ts))
        o_list.append(float(r["open"]) if r.get("open") is not None else float("nan"))
        h_list.append(float(r["high"]) if r.get("high") is not None else float("nan"))
        l_list.append(float(r["low"]) if r.get("low") is not None else float("nan"))
        c_list.append(float(r["close"]) if r.get("close") is not None else float("nan"))
        v_list.append(float(r["volume"]) if r.get("volume") is not None else 0.0)

    df = pd.DataFrame({
        "open": o_list,
        "high": h_list,
        "low": l_list,
        "close": c_list,
        "volume": v_list,
    })

    # / dispatch each requested indicator, skip unknowns/failures silently
    computed: dict = {}
    for ind_id in ids_sorted:
        result = indicator_registry.compute(df, ind_id)
        if result is not None:
            computed[ind_id] = result

    payload = {
        "bars": {
            "t": t_list,
            "o": o_list,
            "h": h_list,
            "l": l_list,
            "c": c_list,
            "v": v_list,
        },
        "indicators": computed,
        "meta": {"symbol": symbol, "timeframe": timeframe, "bar_count": len(rows)},
    }
    if pool_ready:
        _intraday_cache_put(cache_key, payload)
    return payload


# / db schema limits symbol to VARCHAR(20) — reject longer input at the edge so the backend
# / never silently truncates a write (user_chart_state upsert would surface as default-state)
_CHART_STATE_SYMBOL_MAX = 20


@app.get("/api/chart-state/{symbol}")
async def get_chart_state_endpoint(symbol: str):
    # / per-symbol persisted chart state (timeframe + active indicators + params)
    if not symbol or len(symbol) > _CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return {"symbol": symbol, "timeframe": "1Hour", "active_indicators": [], "indicator_params": {}}
    return await chart_state_mod.get_chart_state(STATE.pool, symbol)


@app.post("/api/chart-state/{symbol}")
async def upsert_chart_state_endpoint(symbol: str, body: dict, _auth: None = Depends(require_admin_token)):
    # / upsert chart state for a symbol — only provided fields are updated
    if not symbol or len(symbol) > _CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return {"error": "db_not_ready"}
    # / sanitize indicators against registry to prevent garbage ids
    ids = body.get("active_indicators")
    if ids is not None:
        ids = chart_state_mod.sanitize_indicators(ids)
    params = body.get("indicator_params")
    if params is not None and not isinstance(params, dict):
        params = None
    return await chart_state_mod.upsert_chart_state(
        STATE.pool,
        symbol,
        timeframe=body.get("timeframe"),
        active_indicators=ids,
        indicator_params=params,
    )


@app.get("/api/markers/{symbol}")
async def get_markers_endpoint(
    symbol: str,
    kinds: str = "trades,signals,insiders,earnings,regime,consensus",
    days: int = 30,
):
    # / unified markers endpoint — returns a dict keyed by marker kind
    # / kinds csv filters which aggregators run; absent kinds are omitted from the response
    if not symbol or len(symbol) > _CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return {"trades": [], "signals": [], "insiders": [], "earnings": [], "regime": [], "consensus": []}
    days = max(1, min(days, 365))
    requested = {k.strip() for k in kinds.split(",") if k.strip()}
    if not requested:
        return {}
    return await marker_agg_mod.build_markers(STATE.pool, symbol, requested, days)


@app.get("/api/drawings/{symbol}")
async def list_drawings_endpoint(symbol: str):
    # / list all drawings for a symbol — empty list when db is down
    if not symbol or len(symbol) > _CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return []
    return await drawings_mod.list_drawings(STATE.pool, symbol)


@app.post("/api/drawings/{symbol}")
async def create_drawing_endpoint(symbol: str, body: dict, _auth: None = Depends(require_admin_token)):
    # / create a drawing from a whitelisted type + opaque jsonb payload
    # / accept both `drawing_type` (canonical) and `type` (stale-bundle fallback)
    if not symbol or len(symbol) > _CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return JSONResponse(status_code=503, content={"error": "db_not_ready"})
    raw_type = body.get("drawing_type") or body.get("type") or ""
    dt = drawings_mod.sanitize_drawing_type(raw_type)
    if dt is None:
        return JSONResponse(status_code=400, content={"error": "invalid_drawing_type"})
    payload = body.get("payload")
    if not drawings_mod.validate_payload(payload):
        return JSONResponse(status_code=400, content={"error": "invalid_payload"})
    return await drawings_mod.create_drawing(STATE.pool, symbol, dt, payload)


@app.put("/api/drawings/{symbol}/{drawing_id}")
async def update_drawing_endpoint(symbol: str, drawing_id: int, body: dict, _auth: None = Depends(require_admin_token)):
    # / update a drawing's payload — scoped to symbol, 404 on missing or cross-symbol id
    if not symbol or len(symbol) > _CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return JSONResponse(status_code=503, content={"error": "db_not_ready"})
    payload = body.get("payload")
    if not drawings_mod.validate_payload(payload):
        return JSONResponse(status_code=400, content={"error": "invalid_payload"})
    result = await drawings_mod.update_drawing(STATE.pool, symbol, drawing_id, payload)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return result


@app.delete("/api/drawings/{symbol}/{drawing_id}")
async def delete_drawing_endpoint(symbol: str, drawing_id: int, _auth: None = Depends(require_admin_token)):
    # / delete a single drawing by id — scoped to symbol so a mismatched url cannot bleed across symbols
    if not symbol or len(symbol) > _CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return JSONResponse(status_code=503, content={"error": "db_not_ready"})
    ok = await drawings_mod.delete_drawing(STATE.pool, symbol, drawing_id)
    return {"deleted": bool(ok)}


@app.get("/api/alerts")
async def list_all_alerts_endpoint():
    # / workset for the alert engine — all active rows across every symbol
    if STATE.pool is None:
        return []
    return await alerts_mod.list_alerts(STATE.pool, status=alerts_mod.STATUS_ACTIVE)


@app.get("/api/alerts/{symbol}")
async def list_alerts_endpoint(symbol: str):
    # / active alerts for a single symbol — empty list when db is down
    if not symbol or len(symbol) > _CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return []
    return await alerts_mod.list_alerts(STATE.pool, symbol=symbol, status=alerts_mod.STATUS_ACTIVE)


@app.post("/api/alerts/{symbol}")
async def create_alert_endpoint(symbol: str, body: dict, _auth: None = Depends(require_admin_token)):
    # / create an active alert; 400 on invalid direction/price
    if not symbol or len(symbol) > _CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return JSONResponse(status_code=503, content={"error": "db_not_ready"})
    result = await alerts_mod.create_alert(
        STATE.pool,
        symbol,
        body.get("price"),
        body.get("direction"),
        body.get("label"),
    )
    if isinstance(result, dict) and result.get("error"):
        return JSONResponse(status_code=400, content=result)
    return result


@app.put("/api/alerts/{symbol}/{alert_id}")
async def update_alert_endpoint(symbol: str, alert_id: int, body: dict, _auth: None = Depends(require_admin_token)):
    # / partial update of an alert row; scoped to symbol, 404 on missing or cross-symbol id
    if not symbol or len(symbol) > _CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return JSONResponse(status_code=503, content={"error": "db_not_ready"})
    patch = {k: body[k] for k in ("price", "direction", "label", "status") if k in body}
    if not patch:
        return JSONResponse(status_code=400, content={"error": "empty_patch"})
    result = await alerts_mod.update_alert(STATE.pool, symbol, alert_id, **patch)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return result


@app.delete("/api/alerts/{symbol}/{alert_id}")
async def delete_alert_endpoint(symbol: str, alert_id: int, _auth: None = Depends(require_admin_token)):
    # / hard delete by id — scoped to symbol so a mismatched url cannot bleed across symbols
    if not symbol or len(symbol) > _CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return JSONResponse(status_code=503, content={"error": "db_not_ready"})
    ok = await alerts_mod.delete_alert(STATE.pool, symbol, alert_id)
    return {"deleted": bool(ok)}


@app.get("/api/replay/{symbol}")
async def replay_endpoint(symbol: str, cutoff: str = "", days_back: int = 30):
    # / observation-only: returns bars + trades + signals + consensus knowable at time t
    # / zero re-simulation. zero agent invocation. zero side effects on live state.
    # / do NOT add calls to strategy_agent / risk_agent / executor_agent / particle filter
    # / or any llm client here — phase 1 scope contract locks this to pure SELECT queries
    if not symbol or len(symbol) > _CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return {
            "symbol": symbol,
            "cutoff": cutoff,
            "min_t": None,
            "max_t": None,
            "bars": {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []},
            "trades": [],
            "signals": [],
            "consensus": [],
        }
    return await replay_mod.fetch_replay_snapshot(STATE.pool, symbol, cutoff, days_back)


@app.get("/api/compare")
async def compare_endpoint(
    base: str = "",
    against: str = "",
    symbols: str = "",
    timeframe: str = "1Day",
    days: int = 90,
):
    # / pair normalized overlay — % change from first common timestamp for both symbols
    # / empty series on any failure so the chart can fall back cleanly
    # / accept symbols=AAPL,MSFT as an alias for base=AAPL&against=MSFT
    if not base and not against and symbols:
        parts = [s.strip() for s in symbols.split(",") if s.strip()]
        if len(parts) >= 2:
            base, against = parts[0], parts[1]
    if not base or not against:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if len(base) > _CHART_STATE_SYMBOL_MAX or len(against) > _CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return {
            "base": base,
            "against": against,
            "timeframe": timeframe,
            "days": days,
            "base_series": [],
            "against_series": [],
            "common_count": 0,
        }
    return await compare_mod.fetch_compare(STATE.pool, base, against, timeframe, days)


@app.get("/api/volume-profile/{symbol}")
async def volume_profile_endpoint(symbol: str, bins: int = 24, days: int = 30, timeframe: str = "1Hour"):
    # / horizontal histogram of traded volume at price levels + poc/vah/val anchors
    # / empty payload on pool none or query failure; clamps applied inside the helper
    if not symbol or len(symbol) > _CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return {
            "symbol": symbol,
            "bins": [],
            "poc": None,
            "vah": None,
            "val": None,
            "total_volume": 0.0,
            "bin_count": bins,
            "days": days,
            "timeframe": timeframe,
        }
    return await volume_profile_mod.fetch_volume_profile(STATE.pool, symbol, bins, days, timeframe)


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
    # / broaden the join — strategy scored this symbol via any signal OR actual trade
    # / also include strategy_positions so open-only positions (no closes yet) surface
    # / DISTINCT ON picks the latest strategy_scores row per strategy_id
    rows = await _query(
        """SELECT DISTINCT ON (ss.strategy_id) ss.*
        FROM strategy_scores ss
        WHERE ss.strategy_id IN (
            SELECT strategy_id FROM trade_signals WHERE symbol = $1 AND strategy_id IS NOT NULL
            UNION
            SELECT strategy_id FROM trade_log WHERE symbol = $1 AND strategy_id IS NOT NULL
            UNION
            SELECT strategy_id FROM strategy_positions WHERE symbol = $1
              AND strategy_id IS NOT NULL AND strategy_id <> 'untracked' AND qty > 0
        )
        ORDER BY ss.strategy_id, ss.created_at DESC""",
        symbol,
    )
    # / prior stub emitted null-valued rows that rendered as 0.00/0%/0.000 on the ui,
    # / making strategies look dead when they were actually paper-trading with no closed trades.
    # / preferred path: no row unless we have real metrics; ui shows informative empty state.
    rows_sorted = sorted(
        rows,
        key=lambda r: float(r.get("sharpe_ratio") or -999),
        reverse=True,
    )
    return _serialize(rows_sorted)


@app.get("/api/strategy-evaluations")
async def get_strategy_evaluations(limit: int = 20):
    limit = max(1, min(limit, 100))
    rows = await _query(
        """SELECT * FROM strategy_evaluations
        ORDER BY created_at DESC LIMIT $1""",
        limit,
    )
    return _serialize(rows)


@app.get("/api/observation-log")
async def get_observation_log(hours: int = 24, limit: int = 20):
    # / near-miss tracker (migration 052). grouped counts per strategy so the
    # / analysis tab can show "close to firing" without scanning raw rows.
    hours = max(1, min(hours, 168))
    limit = max(1, min(limit, 100))
    by_strategy = await _query(
        """SELECT strategy_id,
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE near_miss_type = 'n_minus_1_technical') AS n_minus_1,
            COUNT(*) FILTER (WHERE near_miss_type = 'fundamental_gate') AS fundamental_gate,
            MAX(created_at) AS last_seen
        FROM observation_log
        WHERE created_at >= NOW() - ($1 || ' hours')::interval
        GROUP BY strategy_id
        ORDER BY COUNT(*) DESC""",
        str(hours),
    )
    recent = await _query(
        """SELECT strategy_id, symbol, near_miss_type, passed_count, total_count,
            failed_reason, created_at
        FROM observation_log
        WHERE created_at >= NOW() - ($1 || ' hours')::interval
        ORDER BY created_at DESC
        LIMIT $2""",
        str(hours), limit,
    )
    return {
        "hours": hours,
        "by_strategy": _serialize(by_strategy),
        "recent": _serialize(recent),
    }


@app.get("/api/signal-funnel")
async def get_signal_funnel(hours: int = 24):
    # / break down the signal -> approved -> trade path over the last N hours so
    # / "generated 3 signals/cycle, 0 trades today" is answerable on the ui.
    # / rejection_reason was added in migration 051; pre-existing rejected rows
    # / read as "(unknown)" in the breakdown until the agent tags them.
    hours = max(1, min(hours, 168))
    status_rows = await _query(
        """SELECT COALESCE(status, 'pending') AS status, COUNT(*) AS n
        FROM trade_signals
        WHERE created_at >= NOW() - ($1 || ' hours')::interval
        GROUP BY status""",
        str(hours),
    )
    reason_rows = await _query(
        """SELECT COALESCE(rejection_reason, '(untagged)') AS reason, COUNT(*) AS n
        FROM trade_signals
        WHERE status = 'rejected'
          AND created_at >= NOW() - ($1 || ' hours')::interval
        GROUP BY rejection_reason
        ORDER BY COUNT(*) DESC""",
        str(hours),
    )
    approved_count = await _query_one(
        """SELECT COUNT(*) AS n FROM approved_trades
        WHERE created_at >= NOW() - ($1 || ' hours')::interval""",
        str(hours),
    )
    filled_count = await _query_one(
        """SELECT COUNT(*) AS n FROM trade_log
        WHERE created_at >= NOW() - ($1 || ' hours')::interval""",
        str(hours),
    )
    return {
        "hours": hours,
        "by_status": {r["status"]: int(r["n"]) for r in status_rows},
        "by_rejection_reason": [
            {"reason": r["reason"], "count": int(r["n"])} for r in reason_rows
        ],
        "approved_trades": int((approved_count or {}).get("n") or 0),
        "filled_trades": int((filled_count or {}).get("n") or 0),
    }


# / alt-data endpoints — one per source backed by the source_registry

@app.get("/api/macro-context")
async def get_macro_context():
    # / latest FRED indicators (DFF, CPI, UNRATE, 10Y, 2Y, yield spread)
    # / returns normalized values in [-1, 1] + absolute values + timestamp
    if STATE.pool is None:
        return {"indicators": [], "yield_curve_spread": None}
    rows = await _query(
        """SELECT DISTINCT ON (series_id) series_id, date, value, normalized
        FROM macro_data
        ORDER BY series_id, date DESC"""
    )
    by_series = {r["series_id"]: r for r in rows}
    # / derive 10y-2y spread from latest values if both are present
    spread = None
    dgs10 = by_series.get("DGS10")
    dgs2 = by_series.get("DGS2")
    if dgs10 and dgs2:
        try:
            raw = float(dgs10["value"]) - float(dgs2["value"])
            spread = {
                "value": round(raw, 3),
                "normalized": round(max(-1.0, min(1.0, raw / 2.0)), 3),
                "inverted": raw < 0,
            }
        except (TypeError, ValueError):
            spread = None
    return {
        "indicators": _serialize(rows),
        "yield_curve_spread": spread,
    }


@app.get("/api/feature-benchmark")
async def get_feature_benchmark(symbol: str = "SPY"):
    # / run the handbuilt vs alpha158 a/b on one symbol's 5y daily bars
    # / cached per symbol for an hour so the button is cheap to press repeatedly
    import time as _time
    cache_key = symbol.upper()
    now = _time.time()
    hit = STATE.feature_bench_cache.get(cache_key)
    if hit and (now - hit["ts"]) < 3600:
        return hit["result"]

    if STATE.pool is None:
        return {"error": "no_db"}
    rows = await _query(
        """SELECT date, open, high, low, close, volume FROM market_data
        WHERE symbol = $1 ORDER BY date ASC""",
        cache_key,
    )
    if not rows or len(rows) < 400:
        return {"error": "insufficient_history", "rows": len(rows)}
    import pandas as pd
    df = pd.DataFrame([{
        "open":   float(r["open"]) if r["open"] is not None else 0.0,
        "high":   float(r["high"]) if r["high"] is not None else 0.0,
        "low":    float(r["low"]) if r["low"] is not None else 0.0,
        "close":  float(r["close"]) if r["close"] is not None else 0.0,
        "volume": float(r["volume"]) if r["volume"] is not None else 0.0,
    } for r in rows], index=pd.DatetimeIndex([r["date"] for r in rows]))

    from src.quant.ml_signals import benchmark_feature_sets
    result = await benchmark_feature_sets(df)
    result["symbol"] = cache_key
    STATE.feature_bench_cache[cache_key] = {"ts": now, "result": result}
    return result


@app.get("/api/hydration-status")
async def get_hydration_status():
    # / wiki symbol-doc hydration progress: today's count, daily cap, next fire
    from src.data.loop_registry import describe_loops
    cap_raw = os.environ.get("WIKI_HYDRATION_DAILY_CAP", "5")
    try:
        cap = max(0, int(cap_raw))
    except ValueError:
        cap = 5

    hydrated_today = 0
    last_event_ts = None
    if STATE.pool is not None:
        try:
            row = await _query_one(
                """SELECT COUNT(*) as n, MAX(timestamp) as last_ts
                FROM system_events
                WHERE source='knowledge_hydration'
                AND level='info'
                AND timestamp >= CURRENT_DATE"""
            )
            if row:
                hydrated_today = int(row.get("n") or 0)
                last_event_ts = row.get("last_ts")
        except Exception:
            pass

    loops = await describe_loops(STATE.pool)
    hydration = next((loop for loop in loops if loop["name"] == "knowledge_hydration"), None)
    # / never-fired hydration was rendering as status="ok" while last_fire_ts
    # / was null — a contradiction in the UI. force "pending" until the loop
    # / has produced at least one event.
    raw_status = hydration.get("last_status") if hydration else None
    last_status = "pending" if last_event_ts is None else raw_status
    return {
        "daily_cap": cap,
        "hydrated_today": hydrated_today,
        "last_event_ts": last_event_ts.isoformat() if hasattr(last_event_ts, "isoformat") else last_event_ts,
        "next_fire_ts": hydration.get("next_fire_ts").isoformat()
            if hydration and hasattr(hydration.get("next_fire_ts"), "isoformat")
            else (hydration and hydration.get("next_fire_ts")),
        "last_status": last_status,
    }


@app.get("/api/macro-history")
async def get_macro_history(days: int = 180):
    # / per-series timeseries over the last N days for sparkline rendering.
    # / default 180d so monthly series (CPI, FEDFUNDS, UNRATE) always have data.
    if STATE.pool is None:
        return {"series": {}}
    days = max(7, min(int(days or 180), 730))
    rows = await _query(
        """SELECT series_id, date, value FROM macro_data
        WHERE date >= CURRENT_DATE - ($1::int * INTERVAL '1 day')
        ORDER BY series_id, date ASC""",
        days,
    )
    out: dict[str, list[dict]] = {}
    for r in rows:
        sid = r["series_id"]
        out.setdefault(sid, []).append({
            "date": r["date"].isoformat() if hasattr(r["date"], "isoformat") else r["date"],
            "value": float(r["value"]) if r["value"] is not None else None,
        })
    return {"series": out, "days": days}


@app.get("/api/congressional/{symbol}")
async def get_congressional(symbol: str):
    # / last 20 congressional trades for symbol + computed net_buy_ratio
    sym = symbol.upper()
    if STATE.pool is None:
        return {"trades": [], "net_buy_ratio": 0.0}
    rows = await _query(
        """SELECT filing_date, name, transaction_type, amount_range
        FROM congressional_trades
        WHERE symbol = $1
        ORDER BY filing_date DESC LIMIT 20""",
        sym,
    )
    from src.data.congressional_trades import compute_net_buy_ratio
    ratio = compute_net_buy_ratio(rows) if rows else 0.0
    return {
        "trades": _serialize(rows),
        "net_buy_ratio": round(float(ratio), 3),
    }


@app.get("/api/analyst-ratings/{symbol}")
async def get_analyst_ratings(symbol: str):
    # / consensus_score history from analyst_ratings table
    sym = symbol.upper()
    if STATE.pool is None:
        return {"history": []}
    rows = await _query(
        """SELECT date, strong_buy, buy, hold, sell, strong_sell,
                target_high, target_low, target_mean
        FROM analyst_ratings
        WHERE symbol = $1
        ORDER BY date DESC LIMIT 60""",
        sym,
    )
    # / compute consensus per row (same formula as analyst_ratings.compute_consensus_score)
    from src.data.analyst_ratings import compute_consensus_score
    history = []
    for r in rows:
        rec = {
            "strongBuy": r.get("strong_buy") or 0,
            "buy": r.get("buy") or 0,
            "hold": r.get("hold") or 0,
            "sell": r.get("sell") or 0,
            "strongSell": r.get("strong_sell") or 0,
        }
        consensus = compute_consensus_score(rec)
        serialized = _serialize_one(r)
        if serialized is not None:
            serialized["consensus_score"] = round(consensus, 3)
            history.append(serialized)
    return {"history": history}


@app.get("/api/options/{symbol}")
async def get_options(symbol: str):
    # / iv_rank + put_call_ratio + max_pain history from options_data
    sym = symbol.upper()
    if STATE.pool is None:
        return {"history": [], "latest": None}
    rows = await _query(
        """SELECT date, iv_current, iv_rank, put_call_ratio, max_pain
        FROM options_data
        WHERE symbol = $1
        ORDER BY date DESC LIMIT 60""",
        sym,
    )
    serialized = _serialize(rows)
    latest = serialized[0] if serialized else None
    return {"history": serialized, "latest": latest}


@app.get("/api/short/{symbol}")
async def get_short(symbol: str):
    # / short_pct_float history from short_interest table
    sym = symbol.upper()
    if STATE.pool is None:
        return {"history": [], "latest": None}
    rows = await _query(
        """SELECT date, short_volume, total_volume, short_ratio
        FROM short_interest
        WHERE symbol = $1
        ORDER BY date DESC LIMIT 60""",
        sym,
    )
    serialized = _serialize(rows)
    latest = serialized[0] if serialized else None
    return {"history": serialized, "latest": latest}


@app.get("/api/dark-pool/{symbol}")
async def get_dark_pool(symbol: str):
    # / weekly dark_pool rows for symbol
    sym = symbol.upper()
    if STATE.pool is None:
        return {"history": [], "latest": None}
    rows = await _query(
        """SELECT week_start, ats_volume, total_volume, dark_pool_ratio
        FROM dark_pool
        WHERE symbol = $1
        ORDER BY week_start DESC LIMIT 26""",
        sym,
    )
    serialized = _serialize(rows)
    latest = serialized[0] if serialized else None
    return {"history": serialized, "latest": latest}


@app.get("/api/earnings-revisions/{symbol}")
async def get_earnings_revisions(symbol: str):
    # / recent eps estimate revisions from earnings_revisions table
    sym = symbol.upper()
    if STATE.pool is None:
        return {"history": [], "momentum": 0.0}
    rows = await _query(
        """SELECT period, estimate_date, eps_estimate, revenue_estimate
        FROM earnings_revisions
        WHERE symbol = $1
        ORDER BY estimate_date DESC LIMIT 40""",
        sym,
    )
    # / compute revision momentum using the same helper the analyst uses
    from src.data.earnings_revisions import compute_revision_momentum
    estimates = [
        {"eps_avg": float(r["eps_estimate"]) if r.get("eps_estimate") is not None else None}
        for r in rows
    ]
    momentum = compute_revision_momentum(estimates) if estimates else 0.0
    return {
        "history": _serialize(rows),
        "momentum": round(float(momentum), 3),
    }


@app.get("/api/portfolio/correlation")
async def get_portfolio_correlation():
    # / pairwise correlation matrix of held positions via correlation_monitor
    # / returns matrix + symbol labels + avg correlation + concentration flag
    try:
        broker = _get_broker()
        positions = await broker.get_positions()
    except Exception as exc:
        logger.debug("portfolio_correlation_broker_failed", error=str(exc))
        return {"symbols": [], "matrix": [], "avg_correlation": 0.0, "is_concentrated": False}
    if STATE.pool is None or len(positions) < 2:
        return {"symbols": [s.symbol for s in positions], "matrix": [], "avg_correlation": 0.0, "is_concentrated": False}
    try:
        # / rebuild the matrix here because check_portfolio_correlation only returns summary stats
        import numpy as np

        from src.quant.correlation_monitor import check_portfolio_correlation
        symbols = [p.symbol for p in positions]
        returns_map: dict[str, list[float]] = {}
        async with STATE.pool.acquire() as conn:
            for sym in symbols:
                rows = await conn.fetch(
                    """SELECT close FROM market_data
                    WHERE symbol = $1 ORDER BY date DESC LIMIT 21""",
                    sym,
                )
                if len(rows) >= 10:
                    prices = [float(r["close"]) for r in reversed(rows)]
                    rets = np.diff(prices) / np.array(prices[:-1])
                    returns_map[sym] = rets.tolist()
        if len(returns_map) < 2:
            return {"symbols": symbols, "matrix": [], "avg_correlation": 0.0, "is_concentrated": False}
        min_len = min(len(r) for r in returns_map.values())
        aligned_syms = list(returns_map.keys())
        aligned = np.array([returns_map[s][-min_len:] for s in aligned_syms])
        matrix = np.corrcoef(aligned).tolist()
        alert = await check_portfolio_correlation(STATE.pool, positions)
        return {
            "symbols": aligned_syms,
            "matrix": [[round(float(v), 3) for v in row] for row in matrix],
            "avg_correlation": round(alert.avg_correlation, 3) if alert else 0.0,
            "max_pair": list(alert.max_pair) if alert else [],
            "max_correlation": round(alert.max_correlation, 3) if alert else 0.0,
            "is_concentrated": alert.is_concentrated if alert else False,
        }
    except Exception as exc:
        logger.debug("portfolio_correlation_compute_failed", error=str(exc))
        return {"symbols": [], "matrix": [], "avg_correlation": 0.0, "is_concentrated": False}


@app.get("/api/portfolio/sectors")
async def get_portfolio_sectors():
    # / sector concentration derived from strategy_positions + fundamentals sector mapping
    # / returns per-sector dollar exposure + percent of portfolio
    from src.data.symbols import get_sector
    if STATE.pool is None:
        return {"sectors": [], "total_value": 0.0}
    try:
        broker = _get_broker()
        positions = await broker.get_positions()
        total_value = sum(float(p.market_value or 0) for p in positions)
        by_sector: dict[str, float] = {}
        for p in positions:
            sec = get_sector(p.symbol) or "unknown"
            by_sector[sec] = by_sector.get(sec, 0.0) + float(p.market_value or 0)
        sectors = [
            {
                "sector": sec,
                "value": round(val, 2),
                "pct_of_portfolio": round(val / total_value, 4) if total_value > 0 else 0.0,
            }
            for sec, val in sorted(by_sector.items(), key=lambda kv: kv[1], reverse=True)
        ]
        return {"sectors": sectors, "total_value": round(total_value, 2)}
    except Exception as exc:
        logger.debug("portfolio_sectors_failed", error=str(exc))
        return {"sectors": [], "total_value": 0.0}


# / tail-dependence cache: t-copula refit costs 1.6-2.6s per call. cache by
# / (sorted positions tuple, utc date) so a stable book reads the dashboard
# / value at near-zero cost. capped at 16 entries — multi-day operation
# / wouldn't accumulate forever.
# / tail-dependence cache wrappers around STATE.tail_dep_cache (TTL=300s, max=16)
def _tail_dep_cache_get(key: tuple) -> dict | None:
    return STATE.tail_dep_cache.get(key)


def _tail_dep_cache_put(key: tuple, value: dict) -> None:
    STATE.tail_dep_cache.put(key, value)


@app.get("/api/portfolio/tail-dependence")
async def get_portfolio_tail_dependence():
    if STATE.pool is None:
        return {"lambda_lower": None, "positions_count": 0, "status": "pool_unavailable"}
    try:
        from datetime import datetime, timezone
        broker = _get_broker()
        positions = await broker.get_positions()
        if len(positions) < 2:
            return {"lambda_lower": None, "positions_count": len(positions), "status": "insufficient_positions"}

        position_symbols = sorted(p.symbol for p in positions)
        today = datetime.now(timezone.utc).date()
        cache_key = (tuple(position_symbols), today)
        cached = _tail_dep_cache_get(cache_key)
        if cached is not None:
            return cached

        import numpy as np
        from scipy.stats import rankdata

        from src.quant.copula_models import student_t_copula_fit, tail_dependence_coefficient

        async with STATE.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT symbol, date, close FROM market_data
                WHERE symbol = ANY($1)
                ORDER BY date DESC LIMIT $2""",
                position_symbols, 252 * len(position_symbols),
            )
        if not rows:
            return {"lambda_lower": None, "positions_count": len(positions), "status": "no_data"}
        import pandas as pd
        df = pd.DataFrame([dict(r) for r in rows])
        pivot = df.pivot_table(index="date", columns="symbol", values="close")
        if pivot.shape[0] < 10 or pivot.shape[1] < 2:
            return {"lambda_lower": None, "positions_count": len(positions), "status": "insufficient_history"}
        returns = pivot.pct_change().dropna()
        if returns.shape[0] < 10:
            return {"lambda_lower": None, "positions_count": len(positions), "status": "insufficient_returns"}
        u_data = np.column_stack([
            rankdata(returns.iloc[:, j]) / (returns.shape[0] + 1)
            for j in range(returns.shape[1])
        ])
        nu, corr = student_t_copula_fit(u_data)
        td = tail_dependence_coefficient("student_t", (nu, corr))
        lam = td.get("lambda_lower", 0.0)
        result = {
            "lambda_lower": round(float(lam), 4),
            "positions_count": len(positions),
            "status": "ok",
            "nu": round(float(nu), 2),
            "threshold": 0.30,
            "is_concentrated": lam > 0.30,
        }
        _tail_dep_cache_put(cache_key, result)
        return result
    except Exception as exc:
        logger.debug("tail_dependence_compute_failed", error=str(exc))
        return {"lambda_lower": None, "positions_count": 0, "status": "compute_failed"}


@app.get("/api/regime-timeline")
async def get_regime_timeline(market: str = "equity", days: int = 180):
    # / daily regime_history rows for a market, plus regime_shifts events inside the window
    if market not in ("equity", "crypto"):
        return JSONResponse({"error": "invalid market"}, status_code=400)
    days = max(1, min(days, 3650))
    if STATE.pool is None:
        return {"market": market, "days": days, "history": [], "shifts": []}
    history = await _query(
        """SELECT date, regime, confidence, volatility_20d, trend_sma50_above_200, drawdown_from_high
        FROM regime_history
        WHERE market = $1 AND date >= CURRENT_DATE - ($2 || ' days')::INTERVAL
        ORDER BY date ASC""",
        market, str(days),
    )
    shifts = await _query(
        """SELECT id, old_regime, new_regime, confidence, wiki_path, detected_at
        FROM regime_shifts
        WHERE market = $1 AND detected_at >= NOW() - ($2 || ' days')::INTERVAL
        ORDER BY detected_at ASC""",
        market, str(days),
    )
    return {
        "market": market,
        "days": days,
        "history": _serialize(history),
        "shifts": _serialize(shifts),
    }


@app.get("/api/costs")
async def get_costs():
    # / api and llm cost tracking
    if not STATE.pool:
        return {"costs": [], "total_usd": 0}
    try:
        async with STATE.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT date, source, call_count, tokens_in, tokens_out, estimated_cost_usd
                FROM api_costs ORDER BY date DESC, source LIMIT 100"""
            )
        costs = [dict(r) for r in rows]
        total = sum(float(r.get("estimated_cost_usd", 0) or 0) for r in costs)
        return {"costs": costs, "total_usd": round(total, 4)}
    except Exception:
        return {"costs": [], "total_usd": 0}


@app.get("/api/staleness")
async def get_staleness():
    # / data source freshness check
    if not STATE.pool:
        return {"sources": []}
    try:
        import math

        from src.data.staleness_monitor import check_all_freshness
        results = await check_all_freshness(STATE.pool)
        # / staleness_monitor uses float('inf') for sources that failed entirely.
        # / json.dumps can't encode inf, so the whole response 500s if any source
        # / errored. clamp to None — the ui already treats unknown as "never".
        def _clean(h: float):
            if h is None:
                return None
            if math.isinf(h) or math.isnan(h):
                return None
            return round(h, 1)
        return {"sources": [
            {"source": s.source, "last_update": str(s.last_update) if s.last_update else None,
             "staleness_hours": _clean(s.staleness_hours), "threshold_hours": s.threshold_hours,
             "is_stale": s.is_stale}
            for s in results
        ]}
    except Exception:
        return {"sources": []}


@app.get("/api/strategy-decay")
async def get_strategy_decay():
    # / strategy performance decay detection
    if not STATE.pool:
        return {"signals": []}
    try:
        from src.analysis.strategy_decay import check_all_decay
        signals = [
            {
                "strategy_id": ds.strategy_id,
                "rolling_sharpe": round(ds.rolling_sharpe, 3),
                "days_below_threshold": ds.days_below_threshold,
                "cusum_triggered": ds.cusum_triggered,
                "recommendation": ds.recommendation,
            }
            for ds in await check_all_decay(STATE.pool)
        ]
        return {"signals": signals}
    except Exception:
        return {"signals": []}


# / loop introspection + admin trigger + env health

@app.get("/api/loops")
async def get_loops():
    # / one row per known orchestrator loop with cadence, last fire, next fire, status
    from src.data.loop_registry import describe_loops
    rows = await describe_loops(STATE.pool)
    return {"loops": _serialize(rows)}


@app.post("/api/admin/trigger/{service}")
async def admin_trigger(service: str, request: Request):
    # / queue a one-shot run of {service} for the orchestrator to pick up
    # / gated by ADMIN_TOKEN env; pass via Authorization: Bearer <token> header
    # / secure-by-default: missing ADMIN_TOKEN → 503, not open access
    from src.data.loop_registry import LOOP_METADATA, enqueue_trigger
    if not STATE.admin_token:
        return JSONResponse(
            {"error": "admin_token_not_configured",
             "hint": "set ADMIN_TOKEN in .env (>=32 chars) to enable this endpoint"},
            status_code=503,
        )
    supplied = _extract_bearer(request)
    if not _check_admin_token(supplied):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if service not in LOOP_METADATA:
        return JSONResponse({"error": "unknown_service", "service": service}, status_code=404)
    row_id = await enqueue_trigger(STATE.pool, service)
    if row_id is None:
        return JSONResponse({"error": "enqueue_failed"}, status_code=500)
    return JSONResponse({"trigger_id": row_id, "service": service, "status": "queued"}, status_code=202)


@app.get("/api/env-health")
async def get_env_health():
    # / presence check only — never returns values
    # / required = things the system cannot do its core job without.
    # / OLLAMA_BASE_URL only gates wiki embeddings (soft-skip) and TRADE_SYMBOLS
    # / is a universe override that falls back to strategy configs — neither
    # / belongs in required.
    required = [
        "DATABASE_URL", "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
        "GROQ_API_KEY", "DEEPSEEK_API_KEY", "CEREBRAS_API_KEY",
        "FRED_API_KEY", "FINNHUB_API_KEY", "SEC_EDGAR_USER_AGENT",
    ]
    optional = [
        "OLLAMA_BASE_URL", "TRADE_SYMBOLS",
        "DUNE_API_KEY", "DISCORD_WEBHOOK_URL", "SLACK_WEBHOOK_URL",
        "TELEGRAM_BOT_TOKEN", "ADMIN_TOKEN", "KRONOS_ENABLED",
        "WIKI_HYDRATION_DAILY_CAP", "MAX_POSITION_PCT", "CONSENSUS_MODE",
        "ML_FEATURE_SET",
    ]
    return {
        "required": {k: bool(os.environ.get(k)) for k in required},
        "optional": {k: bool(os.environ.get(k)) for k in optional},
    }


# / websocket for live updates

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    origin = ws.headers.get("origin")
    if origin and origin not in STATE.cors_origins:
        await ws.close(code=1008, reason="origin not allowed")
        return

    await ws.accept()
    STATE.ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        STATE.ws_clients.discard(ws)


async def broadcast(event_type: str, data: dict) -> None:
    # / push event to all connected websocket clients
    message = json.dumps({"type": event_type, "data": _serialize_one(data)})
    disconnected = set()
    for ws in STATE.ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)
    STATE.ws_clients.difference_update(disconnected)


# / serialization helpers for decimal/date/datetime types

def _serialize(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        s = _serialize_one(r)
        if s is not None:
            out.append(s)
    return out


def _serialize_one(row: dict | None) -> dict | None:
    if row is None:
        return None
    result = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            result[k] = v.isoformat()
        elif isinstance(v, (int, float, str, bool, type(None), dict, list)):
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

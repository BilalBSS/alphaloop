from __future__ import annotations

import os
import time as _time
import traceback

import asyncpg
import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.dashboard.helpers import db, serializers
from src.dashboard.state import STATE

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/api/phase5-metrics")
async def get_phase5_metrics():
    from src.agents.analyst_agent import get_coverage_pct
    from src.agents.phase5_metrics import compute_phase5_metrics
    from src.data.loop_registry import fetch_service_state
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
            from src.quant.kronos_signal import get_load_status as kronos_status
            kronos_payload = kronos_status()

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


@router.get("/api/symbols")
async def get_symbols():
    from src.data.symbols import FULL_UNIVERSE
    scored = await db.query(
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
    return serializers.serialize(full)


@router.get("/api/strategy-evaluations")
async def get_strategy_evaluations(limit: int = 20):
    limit = max(1, min(limit, 100))
    rows = await db.query(
        """SELECT * FROM strategy_evaluations
        ORDER BY created_at DESC LIMIT $1""",
        limit,
    )
    return serializers.serialize(rows)


@router.get("/api/signal-funnel")
async def get_signal_funnel(hours: int = 24):
    hours = max(1, min(hours, 168))
    status_rows = await db.query(
        """SELECT COALESCE(status, 'pending') AS status, COUNT(*) AS n
        FROM trade_signals
        WHERE created_at >= NOW() - ($1 || ' hours')::interval
        GROUP BY status""",
        str(hours),
    )
    reason_rows = await db.query(
        """SELECT COALESCE(rejection_reason, '(untagged)') AS reason, COUNT(*) AS n
        FROM trade_signals
        WHERE status = 'rejected'
          AND created_at >= NOW() - ($1 || ' hours')::interval
        GROUP BY rejection_reason
        ORDER BY COUNT(*) DESC""",
        str(hours),
    )
    approved_count = await db.query_one(
        """SELECT COUNT(*) AS n FROM approved_trades
        WHERE created_at >= NOW() - ($1 || ' hours')::interval""",
        str(hours),
    )
    filled_count = await db.query_one(
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


@router.get("/api/feature-benchmark")
async def get_feature_benchmark(symbol: str = "SPY"):
    cache_key = symbol.upper()
    now = _time.time()
    hit = STATE.feature_bench_cache.get(cache_key)
    if hit and (now - hit["ts"]) < 3600:
        return hit["result"]

    if STATE.pool is None:
        return {"error": "no_db"}
    rows = await db.query(
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


@router.get("/api/hydration-status")
async def get_hydration_status():
    from src.data.loop_registry import describe_loops
    cap_raw = os.environ.get("WIKI_HYDRATION_DAILY_CAP", "5")
    try:
        cap = max(0, int(cap_raw))
    except ValueError:
        cap = 5

    hydrated_today = 0
    if STATE.pool is not None:
        try:
            row = await db.query_one(
                """SELECT COUNT(*) as n
                FROM system_events
                WHERE source='knowledge_hydration'
                AND level='info'
                AND timestamp >= CURRENT_DATE"""
            )
            if row:
                hydrated_today = int(row.get("n") or 0)
        except (asyncpg.PostgresError, KeyError, ValueError):
            pass

    loops = await describe_loops(STATE.pool)
    hydration = next((loop for loop in loops if loop["name"] == "knowledge_hydration"), None)
    last_fire = hydration.get("last_fire_ts") if hydration else None
    raw_status = hydration.get("last_status") if hydration else None
    last_status = raw_status if last_fire is not None else "pending"
    next_fire = hydration.get("next_fire_ts") if hydration else None
    return {
        "daily_cap": cap,
        "hydrated_today": hydrated_today,
        "last_event_ts": last_fire.isoformat() if hasattr(last_fire, "isoformat") else last_fire,
        "next_fire_ts": next_fire.isoformat() if hasattr(next_fire, "isoformat") else next_fire,
        "last_status": last_status,
    }


@router.get("/api/costs")
async def get_costs():
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
    except (asyncpg.PostgresError, KeyError, ValueError, TypeError):
        return {"costs": [], "total_usd": 0}


@router.get("/api/staleness")
async def get_staleness():
    if not STATE.pool:
        return {"sources": []}
    try:
        import math

        from src.data.staleness_monitor import check_all_freshness
        results = await check_all_freshness(STATE.pool)
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
    except (asyncpg.PostgresError, ImportError, AttributeError, KeyError):
        return {"sources": []}


@router.get("/api/strategy-decay")
async def get_strategy_decay():
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
    except (asyncpg.PostgresError, ImportError, AttributeError, KeyError):
        return {"signals": []}

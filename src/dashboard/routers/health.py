from __future__ import annotations

import asyncio

from fastapi import APIRouter

from src.dashboard.helpers import db, serializers
from src.dashboard.state import STATE

router = APIRouter()


async def _health_db_ping() -> bool:
    try:
        await db.query_one("SELECT 1 as ok")
        return True
    except Exception:
        return False


async def _health_gather_queries() -> dict:
    keys = [
        "last_trade", "last_evolution", "last_synthesis", "last_eval",
        "symbols_analyzed", "last_llm", "db_size", "tables",
        "conn_stats", "active", "recent_errors", "source_stats",
    ]
    results = await asyncio.gather(
        db.query_one("SELECT created_at FROM trade_log ORDER BY created_at DESC LIMIT 1"),
        db.query_one("SELECT created_at FROM evolution_log ORDER BY created_at DESC LIMIT 1"),
        db.query_one("SELECT date FROM daily_synthesis ORDER BY date DESC LIMIT 1"),
        db.query_one("SELECT created_at FROM strategy_evaluations ORDER BY created_at DESC LIMIT 1"),
        db.query_one(
            """SELECT COUNT(DISTINCT symbol) as cnt FROM analysis_scores
            WHERE date >= CURRENT_DATE"""
        ),
        db.query_one(
            """SELECT symbol, details->>'llm_analysis_groq' as groq,
                    details->>'llm_analysis_deepseek' as deepseek
            FROM analysis_scores WHERE date >= CURRENT_DATE
            ORDER BY date DESC LIMIT 1"""
        ),
        db.query_one("SELECT pg_database_size(current_database()) as size_bytes"),
        db.query(
            """SELECT relname as name,
                pg_total_relation_size(relid) as size_bytes,
                n_live_tup as rows
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC LIMIT 10"""
        ),
        db.query_one(
            """SELECT numbackends, xact_commit, xact_rollback, blks_read, blks_hit
            FROM pg_stat_database WHERE datname = current_database()"""
        ),
        db.query_one("SELECT COUNT(*) as cnt FROM pg_stat_activity WHERE state = 'active'"),
        db.query(
            """SELECT timestamp, source, symbol, message
            FROM system_events WHERE level IN ('error', 'warning')
            ORDER BY timestamp DESC LIMIT 20"""
        ),
        db.query(
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
    for loop_name in (
        "intraday_backfill", "daily_bar_backfill", "price_refresh",
        "fundamentals_backfill", "insider_backfill", "regime_backfill",
        "alert", "alternative_data", "macro_backfill",
    ):
        sources.setdefault(loop_name, {"status": "pending", "last_error": None, "errors_24h": 0})
    return sources


async def _health_last_analysis_ts() -> str | None:
    # / canonical analyst timestamp from loop_registry
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


@router.get("/api/health")
async def get_health():
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
        "recent_errors": serializers.serialize(q["recent_errors"] or []),
    }

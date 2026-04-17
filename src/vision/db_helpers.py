# / db helpers for chart_analyses — kept out of agents/tools.py to mirror knowledge/db_helpers.py

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import structlog

from src.vision.gemini_client import ChartAnalysis

logger = structlog.get_logger(__name__)


def _format_vector(vec: list[float]) -> str:
    # / pgvector literal string form, same format used by knowledge.vector_store
    return "[" + ",".join(f"{float(v):.8g}" for v in vec) + "]"


def _to_decimal_array(values: list[float]) -> list[Decimal]:
    # / asyncpg accepts list[Decimal] for decimal[] columns
    return [Decimal(str(round(float(v), 4))) for v in values]


async def store_chart_analysis(
    pool,
    symbol: str,
    timeframe: str,
    image_path: str | None,
    analysis: ChartAnalysis,
    embedding: list[float] | None,
) -> int:
    # / insert a chart_analyses row, returns new id; embedding nullable if ollama failed
    patterns_json = json.dumps(analysis.patterns or [])
    vec_literal = _format_vector(embedding) if embedding else None
    supports = _to_decimal_array(analysis.support_levels or [])
    resistances = _to_decimal_array(analysis.resistance_levels or [])
    bullish_score = Decimal(str(round(float(analysis.bullish_score), 2)))

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO chart_analyses
                (symbol, timeframe, image_path, analysis_text, patterns_detected,
                 trend, support_levels, resistance_levels, bullish_score, embedding)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10::vector)
            RETURNING id
            """,
            symbol, timeframe, image_path, analysis.analysis_text,
            patterns_json, analysis.trend,
            supports, resistances, bullish_score, vec_literal,
        )
    new_id = int(row["id"])
    logger.info(
        "chart_analysis_stored",
        id=new_id, symbol=symbol, timeframe=timeframe,
        trend=analysis.trend, bullish=float(analysis.bullish_score),
        embedded=embedding is not None,
    )
    return new_id


async def fetch_latest_chart_analysis(
    pool,
    symbol: str,
    max_age_hours: int = 36,
) -> dict | None:
    # / return newest chart_analyses row for symbol within window, excludes raw embedding
    cutoff = datetime.now(timezone.utc) - timedelta(hours=int(max_age_hours))
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, symbol, timeframe, image_path, analysis_text, patterns_detected,
                   trend, support_levels, resistance_levels, bullish_score, created_at
            FROM chart_analyses
            WHERE symbol = $1 AND created_at >= $2
            ORDER BY created_at DESC
            LIMIT 1
            """,
            symbol, cutoff,
        )
    if not row:
        return None

    out = dict(row)
    # / coerce decimal arrays + bullish_score to python floats for llm consumption
    out["support_levels"] = [float(v) for v in (out.get("support_levels") or [])]
    out["resistance_levels"] = [float(v) for v in (out.get("resistance_levels") or [])]
    if out.get("bullish_score") is not None:
        try:
            out["bullish_score"] = float(out["bullish_score"])
        except (TypeError, ValueError):
            out["bullish_score"] = None
    # / patterns_detected may come back as str or list depending on asyncpg jsonb codec state
    patterns = out.get("patterns_detected")
    if isinstance(patterns, str):
        try:
            out["patterns_detected"] = json.loads(patterns)
        except Exception:
            out["patterns_detected"] = []
    elif patterns is None:
        out["patterns_detected"] = []
    return out


async def _has_recent_chart_analysis(
    pool,
    symbol: str,
    timeframe: str,
    max_age_hours: int,
) -> bool:
    # / freshness check for chart_analyzer — avoid duplicate gemini calls
    cutoff = datetime.now(timezone.utc) - timedelta(hours=int(max_age_hours))
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT 1 FROM chart_analyses
            WHERE symbol = $1 AND timeframe = $2 AND created_at >= $3
            LIMIT 1""",
            symbol, timeframe, cutoff,
        )
    return row is not None

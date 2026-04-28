# / analysis scores, event log, near-miss observations, fire-and-forget util

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from datetime import date
from decimal import Decimal
from typing import Any

import structlog

from src.strategies.base_strategy import AnalysisData

logger = structlog.get_logger(__name__)

# / strong refs prevent gc of fire-and-forget tasks
_BG_TASKS: set[asyncio.Task] = set()


def fire_and_forget(coro: Coroutine) -> asyncio.Task:
    # / spawn background task that won't be gc'd mid-flight
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


async def store_analysis_score(
    pool, symbol: str, as_of: date, fundamental_score: float | None,
    technical_score: float | None, composite_score: float | None,
    regime: str | None, regime_confidence: float | None,
    used_fundamentals: bool, details: dict[str, Any] | None = None,
) -> int:
    # / upsert analysis_scores row, returns id
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO analysis_scores (symbol, date, fundamental_score, technical_score,
                composite_score, regime, regime_confidence, used_fundamentals, details)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (symbol, date) DO UPDATE SET
                fundamental_score = EXCLUDED.fundamental_score,
                technical_score = EXCLUDED.technical_score,
                composite_score = EXCLUDED.composite_score,
                regime = EXCLUDED.regime,
                regime_confidence = EXCLUDED.regime_confidence,
                used_fundamentals = EXCLUDED.used_fundamentals,
                details = COALESCE(analysis_scores.details, '{}'::jsonb) || COALESCE(EXCLUDED.details, '{}'::jsonb)
                ,created_at = NOW()
            RETURNING id
            """,
            symbol, as_of,
            Decimal(str(fundamental_score)) if fundamental_score is not None else None,
            Decimal(str(technical_score)) if technical_score is not None else None,
            Decimal(str(composite_score)) if composite_score is not None else None,
            regime, Decimal(str(regime_confidence)) if regime_confidence is not None else None,
            used_fundamentals,
            details if details else None,
        )
        return row["id"]


async def fetch_analysis_score(
    pool, symbol: str, as_of: date | None = None,
) -> dict | None:
    # / latest analysis_scores row for symbol
    as_of = as_of or date.today()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM analysis_scores
            WHERE symbol = $1 AND date <= $2
            ORDER BY date DESC LIMIT 1""",
            symbol, as_of,
        )
    return dict(row) if row else None


def dict_to_analysis_data(d: dict) -> AnalysisData:
    # / deserialize jsonb dict to AnalysisData
    return AnalysisData(
        pe_ratio=d.get("pe_ratio"),
        pe_forward=d.get("pe_forward"),
        ps_ratio=d.get("ps_ratio"),
        peg_ratio=d.get("peg_ratio"),
        revenue_growth=d.get("revenue_growth"),
        fcf_margin=d.get("fcf_margin"),
        debt_to_equity=d.get("debt_to_equity"),
        sector_pe_avg=d.get("sector_pe_avg"),
        sector_ps_avg=d.get("sector_ps_avg"),
        dcf_upside=d.get("dcf_upside"),
        insider_net_buy_ratio=d.get("insider_net_buy_ratio"),
        earnings_surprise_pct=d.get("earnings_surprise_pct"),
        consecutive_beats=d.get("consecutive_beats", 0),
        fundamental_score=d.get("fundamental_score"),
        nvt_ratio=d.get("nvt_ratio"),
        funding_rate=d.get("funding_rate"),
        exchange_flow_ratio=d.get("exchange_flow_ratio"),
        news_sentiment_score=d.get("news_sentiment_score"),
        ai_consensus=d.get("ai_consensus") or "neutral",
        regime=d.get("regime"),
        macro_score=d.get("macro_score"),
        congressional_buy_ratio=d.get("congressional_buy_ratio"),
        analyst_consensus=d.get("analyst_consensus"),
        price_target_upside=d.get("price_target_upside"),
        earnings_revision_momentum=d.get("earnings_revision_momentum"),
        short_pct_float=d.get("short_pct_float"),
        dark_pool_ratio=d.get("dark_pool_ratio"),
        iv_rank=d.get("iv_rank"),
        put_call_ratio=d.get("put_call_ratio"),
        days_to_earnings=d.get("days_to_earnings"),
        intermarket_score=d.get("intermarket_score"),
        sector_relative_strength=d.get("sector_relative_strength"),
        hurst=d.get("hurst"),
    )


async def log_event(
    pool, level: str, source: str, message: str,
    symbol: str | None = None, details: dict | None = None,
) -> None:
    # / fire-and-forget event log — never blocks pipeline
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO system_events (level, source, symbol, message, details)
                VALUES ($1, $2, $3, $4, $5::jsonb)""",
                level, source, symbol, message,
                json.dumps(details) if details else None,
            )
    except Exception as exc:
        logger.warning("log_event_failed", source=source, error=str(exc))


async def log_observation(
    pool, strategy_id: str, symbol: str, near_miss_type: str,
    passed_count: int | None = None, total_count: int | None = None,
    strength: float | None = None, failed_reason: str | None = None,
    regime: str | None = None,
) -> None:
    # / fire-and-forget near-miss log; powers "close to firing" panel
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO observation_log
                (strategy_id, symbol, near_miss_type, passed_count, total_count,
                 strength, failed_reason, regime)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                strategy_id, symbol, near_miss_type, passed_count, total_count,
                Decimal(str(strength)) if strength is not None else None,
                failed_reason, regime,
            )
    except Exception as exc:
        logger.debug("log_observation_failed", strategy_id=strategy_id, error=str(exc)[:100])

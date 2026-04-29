
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


async def store_strategy_score(
    pool, strategy_id: str, period_start: date, period_end: date,
    sharpe_ratio: float, max_drawdown: float, win_rate: float,
    brier_score: float | None, total_trades: int,
    regime_breakdown: dict | None = None,
    sortino_ratio: float | None = None,
    composite_score: float | None = None,
) -> int:
    # / store backtest/live performance score
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO strategy_scores
            (strategy_id, period_start, period_end, sharpe_ratio, max_drawdown,
             win_rate, brier_score, total_trades, regime_breakdown,
             sortino_ratio, composite_score)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (strategy_id, period_start, period_end)
            DO UPDATE SET sharpe_ratio = EXCLUDED.sharpe_ratio,
                max_drawdown = EXCLUDED.max_drawdown,
                win_rate = EXCLUDED.win_rate,
                brier_score = EXCLUDED.brier_score,
                total_trades = EXCLUDED.total_trades,
                regime_breakdown = EXCLUDED.regime_breakdown,
                sortino_ratio = EXCLUDED.sortino_ratio,
                composite_score = EXCLUDED.composite_score
            RETURNING id""",
            strategy_id, period_start, period_end,
            Decimal(str(sharpe_ratio)), Decimal(str(max_drawdown)),
            Decimal(str(win_rate)),
            Decimal(str(brier_score)) if brier_score is not None else None,
            total_trades,
            regime_breakdown if regime_breakdown else None,
            Decimal(str(sortino_ratio)) if sortino_ratio is not None else None,
            Decimal(str(composite_score)) if composite_score is not None else None,
        )
        return row["id"]


async def fetch_strategy_scores(pool) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM strategy_scores ORDER BY created_at DESC"
        )
    return [dict(r) for r in rows]


async def store_strategy_evaluation(pool, stats: dict[str, Any]) -> int | None:
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO strategy_evaluations
                (total_pairs, entry_hits, blocked_consensus, blocked_threshold,
                 signals_generated, strategies_evaluated, near_misses)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id""",
                stats.get("total", 0),
                stats.get("total", 0) - stats.get("no_entry", 0) - stats.get("insufficient_data", 0),
                stats.get("blocked_consensus", 0),
                stats.get("blocked_threshold", 0),
                stats.get("signals", 0),
                stats.get("strategies_evaluated", 0),
                stats.get("near_misses", []),
            )
            return row["id"] if row else None
    except Exception as exc:
        logger.warning("store_strategy_evaluation_failed", error=str(exc))
        return None


async def store_evolution_log(
    pool, generation: int, action: str, strategy_id: str,
    parent_id: str | None, reason: str, details: dict | None = None,
) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO evolution_log (generation, action, strategy_id, parent_id, reason, details)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id""",
            generation, action, strategy_id, parent_id, reason,
            details if details else None,
        )
        return row["id"]

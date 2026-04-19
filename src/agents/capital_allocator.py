# / phase 6 step 10: kelly-weighted capital allocator
# /
# / the risk agent used to size every position as a fixed pct of equity.
# / with 30+ strategies competing for the same $100k, that sizing ignores
# / performance differences — top-quartile strategies get the same dollars
# / as bottom-quartile ones.
# /
# / this allocator computes a per-strategy weight that blends:
# /   - kelly_fraction from the strategy config (a preference signal)
# /   - a rank_weight derived from composite_score (top quartile = 2.0,
# /     middle = 1.0, bottom = 0.5) — punishes drift, rewards edge
# /   - a trade_count floor so we don't engage kelly until we have 30 trades
# /     of history; thin samples blow up kelly
# /
# / refreshed weekly via the orchestrator. writes to strategy_allocations.
# / risk_agent reads this table when sizing positions.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

# / below this trade count, kelly is too noisy — fall back to half max_position_pct
MIN_TRADES_FOR_KELLY = 30


@dataclass
class StrategyAllocation:
    strategy_id: str
    kelly_fraction: float
    rank_weight: float
    allocated_weight: float
    composite_score: float | None
    trade_count: int


def _rank_weight(rank_pct: float) -> float:
    # / top 25% -> 2.0, middle 50% -> 1.0, bottom 25% -> 0.5
    if rank_pct <= 0.25:
        return 2.0
    if rank_pct >= 0.75:
        return 0.5
    return 1.0


async def _fetch_strategy_rows(pool: asyncpg.Pool) -> list[dict]:
    # / pull every strategy's composite score + kelly_fraction from config + trade count
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT s.strategy_id, s.composite_score,
                       s.config->'position_sizing'->>'kelly_fraction' AS kelly_raw
                FROM (
                    SELECT DISTINCT ON (strategy_id) strategy_id, composite_score, config
                    FROM strategy_scores
                    ORDER BY strategy_id, updated_at DESC
                ) s
                """,
            )
            trade_counts = await conn.fetch(
                """SELECT strategy_id, COUNT(*) as n
                FROM trade_log
                WHERE strategy_id IS NOT NULL
                GROUP BY strategy_id"""
            )
    except Exception as exc:
        logger.warning("allocator_fetch_failed", error=str(exc)[:200])
        return []

    counts = {r["strategy_id"]: int(r["n"]) for r in trade_counts}
    out: list[dict] = []
    for r in rows:
        kelly_raw = r.get("kelly_raw")
        try:
            kelly = float(kelly_raw) if kelly_raw is not None else 0.25
        except (TypeError, ValueError):
            kelly = 0.25
        out.append({
            "strategy_id": r["strategy_id"],
            "composite_score": float(r["composite_score"]) if r["composite_score"] is not None else None,
            "kelly_fraction": max(0.01, min(1.0, kelly)),
            "trade_count": counts.get(r["strategy_id"], 0),
        })
    return out


def _build_allocations(
    rows: list[dict],
    max_position_pct: float,
) -> list[StrategyAllocation]:
    if not rows:
        return []
    # / rank by composite_score desc; missing scores sort to the bottom
    scored = [r for r in rows if r["composite_score"] is not None]
    unscored = [r for r in rows if r["composite_score"] is None]
    scored.sort(key=lambda r: r["composite_score"], reverse=True)
    n = len(scored)
    allocs: list[StrategyAllocation] = []
    for i, r in enumerate(scored):
        rank_pct = i / max(1, n - 1) if n > 1 else 0.0
        rw = _rank_weight(rank_pct)
        # / if we don't have enough trades, drop to half the default cap
        if r["trade_count"] < MIN_TRADES_FOR_KELLY:
            allocated = 0.5 * max_position_pct
        else:
            allocated = r["kelly_fraction"] * rw * max_position_pct
        # / clamp: no single strategy gets more than 3x the default cap, and no less than 0.25x
        allocated = max(0.25 * max_position_pct, min(3.0 * max_position_pct, allocated))
        allocs.append(StrategyAllocation(
            strategy_id=r["strategy_id"],
            kelly_fraction=r["kelly_fraction"],
            rank_weight=rw,
            allocated_weight=round(allocated, 5),
            composite_score=r["composite_score"],
            trade_count=r["trade_count"],
        ))
    # / unscored strategies get the floor
    for r in unscored:
        allocs.append(StrategyAllocation(
            strategy_id=r["strategy_id"],
            kelly_fraction=r["kelly_fraction"],
            rank_weight=1.0,
            allocated_weight=round(0.5 * max_position_pct, 5),
            composite_score=None,
            trade_count=r["trade_count"],
        ))
    return allocs


async def _write_allocations(pool: asyncpg.Pool, allocs: list[StrategyAllocation]) -> int:
    if not allocs or pool is None:
        return 0
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO strategy_allocations (
                strategy_id, kelly_fraction, rank_weight,
                allocated_weight, composite_score, trade_count, computed_at
            ) VALUES ($1, $2, $3, $4, $5, $6, NOW())
            ON CONFLICT (strategy_id) DO UPDATE SET
                kelly_fraction = EXCLUDED.kelly_fraction,
                rank_weight = EXCLUDED.rank_weight,
                allocated_weight = EXCLUDED.allocated_weight,
                composite_score = EXCLUDED.composite_score,
                trade_count = EXCLUDED.trade_count,
                computed_at = NOW()
            """,
            [(
                a.strategy_id, a.kelly_fraction, a.rank_weight,
                a.allocated_weight, a.composite_score, a.trade_count,
            ) for a in allocs],
        )
    return len(allocs)


async def compute_allocations(
    pool: asyncpg.Pool,
    max_position_pct: float = 0.04,
) -> list[StrategyAllocation]:
    # / orchestrator entry point; returns the list it wrote
    rows = await _fetch_strategy_rows(pool)
    allocs = _build_allocations(rows, max_position_pct)
    if allocs:
        await _write_allocations(pool, allocs)
    logger.info(
        "allocator_complete",
        strategies=len(allocs),
        scored=sum(1 for a in allocs if a.composite_score is not None),
        engaged_kelly=sum(1 for a in allocs if a.trade_count >= MIN_TRADES_FOR_KELLY),
    )
    return allocs


async def get_allocation(
    pool: asyncpg.Pool,
    strategy_id: str,
    max_position_pct_default: float = 0.04,
) -> float:
    # / risk_agent calls this on every sizing decision. returns a weight in
    # / roughly [0.01, 3*max_position_pct].
    # /
    # / when no allocation row exists (allocator hasn't run yet, or strategy is
    # / brand new), return the full default — don't penalize strategies the
    # / allocator hasn't seen. the allocator itself applies the undersampled
    # / penalty once it writes rows for every known strategy.
    if pool is None or not strategy_id:
        return max_position_pct_default
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT allocated_weight FROM strategy_allocations WHERE strategy_id=$1",
                strategy_id,
            )
            if row and row["allocated_weight"] is not None:
                return float(row["allocated_weight"])
    except Exception as exc:
        logger.debug("allocator_read_failed", strategy_id=strategy_id, error=str(exc)[:100])
    return max_position_pct_default

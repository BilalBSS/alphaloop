# / kelly-weighted capital allocator
# / writes strategy_allocations weekly

from __future__ import annotations

from dataclasses import dataclass

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

MIN_TRADES_FOR_KELLY = 30

ACTIVE_STATUSES = frozenset({"live", "active", "paper_trading", "paper", "testing"})


@dataclass(frozen=True)
class DynamicCaps:
    active_count: int
    per_position_pct: float
    per_strategy_pct: float


def _count_active_from_disk() -> int:
    from src.strategies.strategy_loader import load_all_configs
    n = 0
    try:
        for strat in load_all_configs():
            status = strat.config.get("metadata", {}).get("status", "")
            if status in ACTIVE_STATUSES:
                n += 1
    except Exception as exc:
        logger.debug("active_count_load_failed", error=str(exc)[:120])
    return n


def dynamic_caps(active_count: int, cfg: dict) -> DynamicCaps:
    # / scale by active count
    target_gross = max(0.1, 1.0 - float(cfg.get("min_cash_reserve_pct", 0.10)))
    n = max(1, int(active_count))
    raw_per_strat = target_gross / n
    pos_max = float(cfg.get("max_position_pct", 0.05))
    pos_min = float(cfg.get("min_position_pct", 0.02))
    strat_max = float(cfg.get("max_exposure_per_strategy_pct", 0.30))
    strat_min = float(cfg.get("min_exposure_per_strategy_pct", 0.10))
    slots = max(1, int(cfg.get("max_positions_per_strategy", 6)))
    per_strat = max(strat_min, min(strat_max, raw_per_strat))
    per_pos = max(pos_min, min(pos_max, per_strat / slots))
    return DynamicCaps(
        active_count=n,
        per_position_pct=round(per_pos, 5),
        per_strategy_pct=round(per_strat, 5),
    )


def get_dynamic_caps(cfg: dict, active_count: int | None = None) -> DynamicCaps:
    n = active_count if active_count is not None else _count_active_from_disk()
    return dynamic_caps(n, cfg)


@dataclass
class StrategyAllocation:
    strategy_id: str
    kelly_fraction: float
    rank_weight: float
    allocated_weight: float
    composite_score: float | None
    trade_count: int


def _rank_weight(rank_pct: float) -> float:
    if rank_pct <= 0.25:
        return 2.0
    if rank_pct >= 0.75:
        return 0.5
    return 1.0


def _load_kelly_fractions_from_disk() -> dict[str, float]:
    from src.strategies.strategy_loader import load_all_configs
    out: dict[str, float] = {}
    try:
        for strat in load_all_configs():
            kelly = strat.config.get("position_sizing", {}).get("kelly_fraction", 0.25)
            try:
                out[strat.strategy_id] = float(kelly)
            except (TypeError, ValueError):
                out[strat.strategy_id] = 0.25
    except Exception as exc:
        logger.warning("kelly_fraction_load_failed", error=str(exc)[:200])
    return out


async def _fetch_strategy_rows(pool: asyncpg.Pool) -> list[dict]:
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (strategy_id) strategy_id, composite_score
                FROM strategy_scores
                ORDER BY strategy_id, created_at DESC
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
    kelly_map = _load_kelly_fractions_from_disk()
    out: list[dict] = []
    for r in rows:
        kelly = kelly_map.get(r["strategy_id"], 0.25)
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
    scored = [r for r in rows if r["composite_score"] is not None]
    unscored = [r for r in rows if r["composite_score"] is None]
    scored.sort(key=lambda r: r["composite_score"], reverse=True)
    n = len(scored)
    allocs: list[StrategyAllocation] = []
    for i, r in enumerate(scored):
        rank_pct = i / max(1, n - 1) if n > 1 else 0.0
        rw = _rank_weight(rank_pct)
        if r["trade_count"] < MIN_TRADES_FOR_KELLY:
            allocated = 0.5 * max_position_pct
        else:
            allocated = r["kelly_fraction"] * rw * max_position_pct
        allocated = max(0.25 * max_position_pct, min(3.0 * max_position_pct, allocated))
        allocs.append(StrategyAllocation(
            strategy_id=r["strategy_id"],
            kelly_fraction=r["kelly_fraction"],
            rank_weight=rw,
            allocated_weight=round(allocated, 5),
            composite_score=r["composite_score"],
            trade_count=r["trade_count"],
        ))
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
    # / weight in [0.01, 3*max_position_pct]
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

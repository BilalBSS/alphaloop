# / db helpers for knowledge tables — kept out of agents/tools.py to avoid clutter

from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


async def store_evolution_mutation(
    pool,
    generation: int,
    parent_strategy_id: str,
    wiki_guided: bool,
    wiki_context_tokens: int,
) -> int:
    # / insert a tracking row before mutation, returns the new row id
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evolution_mutations
                (generation, parent_strategy_id, wiki_guided, wiki_context_tokens)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            int(generation), parent_strategy_id,
            bool(wiki_guided), int(wiki_context_tokens),
        )
    return int(row["id"])


async def update_evolution_mutation_outcome(
    pool,
    row_id: int,
    mutant_strategy_id: str | None = None,
    mutation_diff: dict | None = None,
    parent_sharpe: float | None = None,
    mutant_sharpe: float | None = None,
    sharpe_delta: float | None = None,
    survived: bool | None = None,
) -> None:
    # / patch any mutation-outcome fields that are known at this point
    sets: list[str] = []
    params: list[Any] = []

    def _add(col: str, value: Any) -> None:
        if value is None:
            return
        params.append(value)
        sets.append(f"{col} = ${len(params)}")

    _add("mutant_strategy_id", mutant_strategy_id)
    if mutation_diff is not None:
        # / asyncpg jsonb codec (registered in db.init_db) auto-encodes dicts
        params.append(mutation_diff)
        sets.append(f"mutation_diff = ${len(params)}")
    _add("parent_sharpe", Decimal(str(parent_sharpe)) if parent_sharpe is not None else None)
    _add("mutant_sharpe", Decimal(str(mutant_sharpe)) if mutant_sharpe is not None else None)
    _add("sharpe_delta", Decimal(str(sharpe_delta)) if sharpe_delta is not None else None)
    if survived is not None:
        params.append(bool(survived))
        sets.append(f"survived = ${len(params)}")

    if not sets:
        return

    params.append(int(row_id))
    sql = f"UPDATE evolution_mutations SET {', '.join(sets)} WHERE id = ${len(params)}"
    async with pool.acquire() as conn:
        await conn.execute(sql, *params)


async def update_evolution_mutation_by_mutant(
    pool,
    mutant_strategy_id: str,
    mutant_sharpe: float | None = None,
    sharpe_delta: float | None = None,
    parent_sharpe: float | None = None,
    survived: bool | None = None,
) -> None:
    # / update most-recent evolution_mutations row for a mutant strategy id
    if not mutant_strategy_id:
        return

    sets: list[str] = []
    params: list[Any] = []

    def _add(col: str, value: Any) -> None:
        if value is None:
            return
        params.append(value)
        sets.append(f"{col} = ${len(params)}")

    _add("parent_sharpe", Decimal(str(parent_sharpe)) if parent_sharpe is not None else None)
    _add("mutant_sharpe", Decimal(str(mutant_sharpe)) if mutant_sharpe is not None else None)
    _add("sharpe_delta", Decimal(str(sharpe_delta)) if sharpe_delta is not None else None)
    if survived is not None:
        params.append(bool(survived))
        sets.append(f"survived = ${len(params)}")

    if not sets:
        return

    params.append(mutant_strategy_id)
    sql = (
        f"UPDATE evolution_mutations SET {', '.join(sets)} "
        f"WHERE mutant_strategy_id = ${len(params)}"
    )
    async with pool.acquire() as conn:
        await conn.execute(sql, *params)


async def store_post_mortem_row(
    pool,
    strategy_id: str,
    symbol: str,
    trigger_type: str,
    pnl: float | None,
    expected_pnl: float | None,
    deviation_sigma: float | None,
    details: dict | None,
    wiki_path: str | None,
) -> int:
    # / insert a post_mortems row unconditionally, returns the id
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO post_mortems
                (strategy_id, symbol, trigger_type, pnl, expected_pnl,
                 deviation_sigma, details, wiki_path)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
            """,
            strategy_id, symbol, trigger_type,
            Decimal(str(pnl)) if pnl is not None else None,
            Decimal(str(expected_pnl)) if expected_pnl is not None else None,
            Decimal(str(deviation_sigma)) if deviation_sigma is not None else None,
            details if details else None,
            wiki_path,
        )
    return int(row["id"])


async def claim_post_mortem_slot(
    pool,
    strategy_id: str,
    symbol: str,
    trigger_type: str,
    pnl: float | None,
    expected_pnl: float | None,
    deviation_sigma: float | None,
    details: dict | None,
    cooldown_hours: int = 24,
) -> int | None:
    # / atomic cooldown + insert — closes the TOCTOU window between check & insert
    # / returns row id if the slot was claimed, None if cooldown blocks it
    # / wiki_path starts NULL; callers update it via set_post_mortem_wiki_path after the write
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO post_mortems
                (strategy_id, symbol, trigger_type, pnl, expected_pnl,
                 deviation_sigma, details, wiki_path)
            SELECT $1, $2, $3, $4, $5, $6, $7, NULL
            WHERE NOT EXISTS (
                SELECT 1 FROM post_mortems
                WHERE strategy_id = $1
                  AND created_at > NOW() - make_interval(hours => $8)
            )
            RETURNING id
            """,
            strategy_id, symbol, trigger_type,
            Decimal(str(pnl)) if pnl is not None else None,
            Decimal(str(expected_pnl)) if expected_pnl is not None else None,
            Decimal(str(deviation_sigma)) if deviation_sigma is not None else None,
            details if details else None,
            int(cooldown_hours),
        )
    return int(row["id"]) if row else None


async def set_post_mortem_wiki_path(pool, row_id: int, wiki_path: str | None) -> None:
    # / patch wiki_path on a previously-claimed post_mortems row
    if row_id is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE post_mortems SET wiki_path = $1 WHERE id = $2",
            wiki_path, int(row_id),
        )


async def update_post_mortem_details(pool, row_id: int, details: dict | None) -> None:
    # / patch details JSONB on a previously-claimed post_mortems row
    if row_id is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE post_mortems SET details = $1 WHERE id = $2",
            details if details else None, int(row_id),
        )


async def store_regime_shift_row(
    pool,
    old_regime: str,
    new_regime: str,
    market: str,
    confidence: float | None,
    wiki_path: str | None,
) -> int:
    # / insert a regime_shifts row, returns the id
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO regime_shifts
                (old_regime, new_regime, market, confidence, wiki_path)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            old_regime, new_regime, market,
            Decimal(str(confidence)) if confidence is not None else None,
            wiki_path,
        )
    return int(row["id"])

# / trade log queries
# / lives in data/ because evolution and knowledge import these helpers

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


async def fetch_recent_trades(
    pool, strategy_id: str | None = None, limit: int = 100,
) -> list[dict]:
    # / recent trade_log entries, optional strategy filter
    async with pool.acquire() as conn:
        if strategy_id:
            rows = await conn.fetch(
                """SELECT * FROM trade_log
                WHERE strategy_id = $1
                ORDER BY created_at DESC LIMIT $2""",
                strategy_id, limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM trade_log ORDER BY created_at DESC LIMIT $1",
                limit,
            )
    return [dict(r) for r in rows]


async def count_symbol_trades(pool, strategy_id: str, symbol: str) -> int:
    # / count trades for strategy+symbol combo
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT COUNT(*) as cnt FROM trade_log
            WHERE strategy_id = $1 AND symbol = $2""",
            strategy_id, symbol,
        )
        return int(row["cnt"]) if row else 0


async def count_all_symbol_trades(pool, symbol: str) -> int:
    # / count all trades for a symbol across strategies
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT COUNT(*) as cnt FROM trade_log
            WHERE symbol = $1""",
            symbol,
        )
        return int(row["cnt"]) if row else 0

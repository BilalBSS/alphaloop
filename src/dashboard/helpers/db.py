
from __future__ import annotations

from src.dashboard.state import STATE


async def query(sql: str, *args) -> list[dict]:
    if STATE.pool is None:
        return []
    async with STATE.pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]


async def query_one(sql: str, *args) -> dict | None:
    if STATE.pool is None:
        return None
    async with STATE.pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None

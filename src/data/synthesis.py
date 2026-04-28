# / daily synthesis storage
# / lives in data/ because analysis/ai_summary.py imports these helpers

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


async def store_daily_synthesis(
    pool, date, model: str, top_buys: list | None,
    top_avoids: list | None, portfolio_risk: str | None,
    per_symbol_notes: dict | None, raw_response: str | None,
) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO daily_synthesis (date, model, top_buys, top_avoids,
                portfolio_risk, per_symbol_notes, raw_response)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (date) DO UPDATE SET
                model = EXCLUDED.model, top_buys = EXCLUDED.top_buys,
                top_avoids = EXCLUDED.top_avoids, portfolio_risk = EXCLUDED.portfolio_risk,
                per_symbol_notes = EXCLUDED.per_symbol_notes, raw_response = EXCLUDED.raw_response
            RETURNING id""",
            date, model,
            top_buys if top_buys else None,
            top_avoids if top_avoids else None,
            portfolio_risk,
            per_symbol_notes if per_symbol_notes else None,
            raw_response,
        )
        return row["id"]


async def fetch_daily_synthesis(pool, target_date=None) -> dict | None:
    async with pool.acquire() as conn:
        if target_date:
            row = await conn.fetchrow(
                "SELECT * FROM daily_synthesis WHERE date = $1", target_date,
            )
        else:
            row = await conn.fetchrow(
                "SELECT * FROM daily_synthesis ORDER BY date DESC LIMIT 1",
            )
        return dict(row) if row else None

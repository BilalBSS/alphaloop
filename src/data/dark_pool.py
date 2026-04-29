
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import structlog

from .resilience import api_post, configure_rate_limit, with_retry

logger = structlog.get_logger(__name__)

FINRA_ATS_URL = "https://api.finra.org/data/group/otcMarket/name/weeklySummary"

configure_rate_limit("finra_ats", max_concurrent=2, delay=1.5)


@with_retry(source="finra_ats", max_retries=2, base_delay=2.0)
async def fetch_dark_pool_data(symbol: str, pool: Any | None = None) -> dict[str, Any] | None:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        body = {
            "fields": ["weekStartDate", "totalWeeklyShareQuantity", "totalWeeklyTradeCount"],
            "compareFilters": [
                {"fieldName": "issueSymbolIdentifier", "fieldValue": symbol.upper(), "compareType": "EQUAL"}
            ],
            "limit": 4,
            "sort": [{"fieldName": "weekStartDate", "order": "DESC"}],
        }
        resp = await api_post(FINRA_ATS_URL, headers=headers, json=body, source="finra_ats")
        data = resp.json()
        if not data:
            return None
        latest = data[0]
        ats_volume = latest.get("totalWeeklyShareQuantity", 0) or 0
        week_start_str = latest.get("weekStartDate", "")

        total_volume: int | None = None
        dark_pool_ratio: float | None = None

        if pool is not None and week_start_str:
            try:
                week_start = date.fromisoformat(week_start_str)
                week_end = week_start + timedelta(days=6)
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        """SELECT COALESCE(SUM(volume), 0)::bigint AS total
                        FROM market_data
                        WHERE symbol = $1 AND date >= $2 AND date <= $3""",
                        symbol.upper(), week_start, week_end,
                    )
                total_volume = int(row["total"]) if row and row["total"] else None
                if total_volume and total_volume > 0 and ats_volume > 0:
                    dark_pool_ratio = min(1.0, float(ats_volume) / float(total_volume))
            except Exception as exc:
                logger.debug("dark_pool_total_volume_lookup_failed", symbol=symbol, error=str(exc)[:100])

        return {
            "symbol": symbol,
            "week_start": week_start_str,
            "ats_volume": ats_volume,
            "total_volume": total_volume,
            "dark_pool_ratio": dark_pool_ratio,
        }
    except Exception as exc:
        logger.debug("dark_pool_fetch_failed", symbol=symbol, error=str(exc))
        return None


async def store_dark_pool(pool: Any, data: dict[str, Any]) -> None:
    if not data:
        return
    ws = data.get("week_start")
    if isinstance(ws, str) and ws:
        ws = date.fromisoformat(ws)
    elif not isinstance(ws, date):
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO dark_pool (symbol, week_start, ats_volume, total_volume, dark_pool_ratio)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (symbol, week_start) DO UPDATE SET
                ats_volume = EXCLUDED.ats_volume,
                total_volume = EXCLUDED.total_volume,
                dark_pool_ratio = EXCLUDED.dark_pool_ratio
            """,
            data["symbol"], ws,
            data.get("ats_volume"), data.get("total_volume"),
            data.get("dark_pool_ratio"),
        )

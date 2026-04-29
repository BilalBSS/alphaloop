from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import structlog

from ._serialize import iso as _iso
from ._serialize import num as _num

logger = structlog.get_logger(__name__)

_DAILY_TIMEFRAMES: set[str] = {"1Day", "1day", "1D"}

_DAYS_MIN = 1
_DAYS_MAX = 365
_DAYS_DEFAULT = 90


def _clamp_days(days: Any) -> int:
    try:
        n = int(days)
    except (TypeError, ValueError):
        return _DAYS_DEFAULT
    if n < _DAYS_MIN:
        return _DAYS_MIN
    if n > _DAYS_MAX:
        return _DAYS_MAX
    return n


async def _fetch_closes(
    pool: asyncpg.Pool, symbol: str, timeframe: str, days: int
) -> list[tuple[str, float]]:
    is_daily = timeframe in _DAILY_TIMEFRAMES
    try:
        async with pool.acquire() as conn:
            if is_daily:
                rows = await conn.fetch(
                    """SELECT date, close FROM market_data
                    WHERE symbol = $1 AND date > CURRENT_DATE - ($2 || ' days')::INTERVAL
                    ORDER BY date ASC""",
                    symbol,
                    str(days),
                )
            else:
                rows = await conn.fetch(
                    """SELECT timestamp, close FROM market_data_intraday
                    WHERE symbol = $1 AND timeframe = $2
                        AND timestamp > NOW() - ($3 || ' days')::INTERVAL
                    ORDER BY timestamp ASC""",
                    symbol,
                    timeframe,
                    str(days),
                )
    except asyncpg.PostgresError as exc:
        logger.debug("compare_closes_query_failed", symbol=symbol, error=str(exc))
        return []
    out: list[tuple[str, float]] = []
    for r in rows:
        ts = r.get("date") if is_daily else r.get("timestamp")
        close = _num(r.get("close"))
        if ts is None or close is None:
            continue
        ts_iso = _iso(ts)
        if ts_iso is None:
            continue
        out.append((ts_iso, close))
    return out


async def fetch_compare(
    pool: asyncpg.Pool,
    base: str,
    against: str,
    timeframe: str = "1Day",
    days: int = _DAYS_DEFAULT,
) -> dict:
    days_clamped = _clamp_days(days)
    empty = {
        "base": base,
        "against": against,
        "timeframe": timeframe,
        "days": days_clamped,
        "base_series": [],
        "against_series": [],
        "common_count": 0,
    }
    if pool is None:
        return empty

    base_res, against_res = await asyncio.gather(
        _fetch_closes(pool, base, timeframe, days_clamped),
        _fetch_closes(pool, against, timeframe, days_clamped),
        return_exceptions=True,
    )
    base_closes: list[tuple[str, float]] = [] if isinstance(base_res, BaseException) else base_res
    against_closes: list[tuple[str, float]] = [] if isinstance(against_res, BaseException) else against_res
    if not base_closes or not against_closes:
        return empty

    against_map = {t: c for t, c in against_closes}
    common_pairs: list[tuple[str, float, float]] = []
    for t, bc in base_closes:
        ac = against_map.get(t)
        if ac is None:
            continue
        common_pairs.append((t, bc, ac))
    if not common_pairs:
        return empty

    base_first = common_pairs[0][1]
    against_first = common_pairs[0][2]
    if base_first == 0 or against_first == 0:
        return empty

    base_series = [
        {"time": t, "value": (bc / base_first - 1.0) * 100.0}
        for t, bc, _ac in common_pairs
    ]
    against_series = [
        {"time": t, "value": (ac / against_first - 1.0) * 100.0}
        for t, _bc, ac in common_pairs
    ]
    return {
        "base": base,
        "against": against,
        "timeframe": timeframe,
        "days": days_clamped,
        "base_series": base_series,
        "against_series": against_series,
        "common_count": len(common_pairs),
    }

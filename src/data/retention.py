from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


async def _prune(pool, table: str, ts_col: str, max_age_days: int, log_event: str) -> int:
    if max_age_days <= 0:
        raise ValueError("max_age_days must be positive")
    days = int(max_age_days)
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM {table} WHERE {ts_col} < NOW() - INTERVAL '{days} days'"
            )
        count = int(result.split()[-1]) if result else 0
        if count > 0:
            logger.info(log_event, rows=count, retention_days=days)
        return count
    except Exception as exc:
        msg = str(exc).lower()
        if "does not exist" in msg or "undefined" in msg:
            return 0
        logger.warning(f"{log_event}_failed", error=str(exc)[:120])
        raise


async def prune_observation_log(pool, max_age_days: int = 14) -> int:
    return await _prune(pool, "observation_log", "created_at", max_age_days, "observation_log_pruned")


async def prune_system_events(pool, max_age_days: int = 30) -> int:
    return await _prune(pool, "system_events", "timestamp", max_age_days, "system_events_pruned")

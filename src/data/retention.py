from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


async def prune_observation_log(pool, max_age_days: int = 14) -> int:
    if max_age_days <= 0:
        raise ValueError("max_age_days must be positive")
    days = int(max_age_days)
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM observation_log WHERE created_at < NOW() - INTERVAL '{days} days'"
            )
        count = int(result.split()[-1]) if result else 0
        if count > 0:
            logger.info("observation_log_pruned", rows=count, retention_days=days)
        return count
    except Exception as exc:
        msg = str(exc).lower()
        if "does not exist" in msg or "undefined" in msg:
            return 0
        logger.warning("observation_log_prune_failed", error=str(exc)[:120])
        raise


async def prune_system_events(pool, max_age_days: int = 30) -> int:
    if max_age_days <= 0:
        raise ValueError("max_age_days must be positive")
    days = int(max_age_days)
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM system_events WHERE timestamp < NOW() - INTERVAL '{days} days'"
            )
        count = int(result.split()[-1]) if result else 0
        if count > 0:
            logger.info("system_events_pruned", rows=count, retention_days=days)
        return count
    except Exception as exc:
        msg = str(exc).lower()
        if "does not exist" in msg or "undefined" in msg:
            return 0
        logger.warning("system_events_prune_failed", error=str(exc)[:120])
        raise

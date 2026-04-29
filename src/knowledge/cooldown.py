# / per-strategy post-mortem cooldown guard

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


async def can_write_post_mortem(
    pool, strategy_id: str, hours: int = 24,
) -> bool:
    if not strategy_id:
        return False
    try:
        async with pool.acquire() as conn:
            age_hours = await conn.fetchval(
                """
                SELECT EXTRACT(EPOCH FROM (NOW() - MAX(created_at))) / 3600.0
                FROM post_mortems
                WHERE strategy_id = $1
                """,
                strategy_id,
            )
            if age_hours is None:
                return True
            return float(age_hours) >= float(hours)
    except Exception as exc:
        logger.warning("post_mortem_cooldown_check_failed", strategy_id=strategy_id, error=str(exc)[:120])
        return False

from __future__ import annotations

import json

import structlog

logger = structlog.get_logger(__name__)


async def get_flag(pool, key: str, default=None):
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM system_flags WHERE key = $1", key,
            )
        if row is None:
            return default
        val = row["value"]
    except (KeyError, TypeError, AttributeError) as exc:
        logger.debug("system_flag_read_failed", key=key, error=str(exc)[:120])
        return default
    if isinstance(val, (str, bytes, bytearray)):
        try:
            return json.loads(val)
        except (ValueError, TypeError):
            return default
    return val


async def set_flag(pool, key: str, value) -> None:
    payload = json.dumps(value)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO system_flags (key, value, updated_at)
            VALUES ($1, $2::jsonb, now())
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = now()
            """,
            key, payload,
        )


async def is_executor_paused(pool) -> bool:
    return bool(await get_flag(pool, "executor_paused", default=False))


async def set_executor_paused(pool, paused: bool) -> bool:
    await set_flag(pool, "executor_paused", bool(paused))
    return bool(paused)

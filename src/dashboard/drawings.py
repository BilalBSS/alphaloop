from __future__ import annotations

import json
from typing import Any

import asyncpg
import structlog

from ._serialize import iso as _iso
from ._serialize import whitelist as _whitelist

logger = structlog.get_logger(__name__)

VALID_DRAWING_TYPES: set[str] = {
    "trendline",
    "horizontal_line",
    "vertical_line",
    "rectangle",
    "parallel_channel",
    "fib_retracement",
    "fib_extension",
    "text",
    "arrow",
    "ray",
    "price_range",
    "brush",
}

_PAYLOAD_MAX_BYTES = 32 * 1024


def sanitize_drawing_type(dt: Any) -> str | None:
    return _whitelist(dt, VALID_DRAWING_TYPES)


def validate_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    try:
        encoded = json.dumps(payload)
    except (TypeError, ValueError):
        return False
    return len(encoded) <= _PAYLOAD_MAX_BYTES


def _parse_jsonb(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return None


def _row_to_drawing(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "drawing_type": row.get("drawing_type"),
        "payload": _parse_jsonb(row.get("payload")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


async def list_drawings(pool: asyncpg.Pool, symbol: str) -> list[dict]:
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, drawing_type, payload, created_at, updated_at
                FROM user_chart_drawings
                WHERE symbol = $1
                ORDER BY created_at ASC""",
                symbol,
            )
    except Exception as exc:
        logger.debug("drawings_list_failed", symbol=symbol, error=str(exc))
        return []
    return [_row_to_drawing(dict(r)) for r in rows]


async def create_drawing(pool: asyncpg.Pool, symbol: str, drawing_type: str, payload: dict) -> dict:
    payload_json = json.dumps(payload)
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO user_chart_drawings (symbol, drawing_type, payload, created_at, updated_at)
                VALUES ($1, $2, $3::jsonb, NOW(), NOW())
                RETURNING id, drawing_type, payload, created_at, updated_at""",
                symbol,
                drawing_type,
                payload_json,
            )
    except Exception as exc:
        logger.debug("drawings_create_failed", symbol=symbol, error=str(exc))
        return {"error": "insert_failed"}
    if row is None:
        return {"error": "insert_failed"}
    return _row_to_drawing(dict(row))


async def update_drawing(pool: asyncpg.Pool, symbol: str, drawing_id: int, payload: dict) -> dict | None:
    payload_json = json.dumps(payload)
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE user_chart_drawings
                SET payload = $1::jsonb, updated_at = NOW()
                WHERE id = $2 AND symbol = $3
                RETURNING id, drawing_type, payload, created_at, updated_at""",
                payload_json,
                drawing_id,
                symbol,
            )
    except Exception as exc:
        logger.debug("drawings_update_failed", drawing_id=drawing_id, error=str(exc))
        return None
    if row is None:
        return None
    return _row_to_drawing(dict(row))


async def delete_drawing(pool: asyncpg.Pool, symbol: str, drawing_id: int) -> bool:
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM user_chart_drawings WHERE id = $1 AND symbol = $2",
                drawing_id,
                symbol,
            )
    except Exception as exc:
        logger.debug("drawings_delete_failed", drawing_id=drawing_id, error=str(exc))
        return False
    if isinstance(result, str) and result.startswith("DELETE "):
        try:
            return int(result.split(" ", 1)[1]) > 0
        except (ValueError, IndexError):
            return False
    return False


async def delete_all_drawings(pool: asyncpg.Pool, symbol: str) -> int:
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM user_chart_drawings WHERE symbol = $1",
                symbol,
            )
    except Exception as exc:
        logger.debug("drawings_delete_all_failed", symbol=symbol, error=str(exc))
        return 0
    if isinstance(result, str) and result.startswith("DELETE "):
        try:
            return int(result.split(" ", 1)[1])
        except (ValueError, IndexError):
            return 0
    return 0

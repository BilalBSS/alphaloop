# / user drawing persistence — trendlines, fibs, rects, hlines, vlines, text
# / payload is opaque jsonb coming from lightweight-charts-drawing serialization
from __future__ import annotations

import json
from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

# / whitelist of drawing_type values accepted from the client
# / keeps garbage out of the table and prevents users from inflating the type column
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

# / payload size cap — payloads are anchor lists + style dicts, real drawings fit in a few kb
# / 32kb is comfortable for extremes (brushes with many vertices) while rejecting pathological inputs
_PAYLOAD_MAX_BYTES = 32 * 1024


def sanitize_drawing_type(dt: Any) -> str | None:
    # / normalize + whitelist check, returns None when the type is unknown
    if not isinstance(dt, str):
        return None
    normalized = dt.strip().lower()
    if normalized not in VALID_DRAWING_TYPES:
        return None
    return normalized


def validate_payload(payload: Any) -> bool:
    # / minimal sanity: must be dict, serializable, under the size cap
    if not isinstance(payload, dict):
        return False
    try:
        encoded = json.dumps(payload)
    except (TypeError, ValueError):
        return False
    return len(encoded) <= _PAYLOAD_MAX_BYTES


def _parse_jsonb(value: Any) -> Any:
    # / asyncpg returns jsonb as native dict/list or str depending on driver config — normalize
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _row_to_drawing(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "drawing_type": row.get("drawing_type"),
        "payload": _parse_jsonb(row.get("payload")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


async def list_drawings(pool: asyncpg.Pool, symbol: str) -> list[dict]:
    # / all drawings for a symbol, oldest first so clients render them in creation order
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
    # / insert a new drawing row and return the serialized shape
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
    # / update payload for an existing drawing scoped to symbol, returns None when the row is missing
    # / scoping prevents a cross-symbol mutation via a mismatched url segment
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
    # / delete a single drawing by id scoped to symbol, returns true on hit
    # / scoping prevents a cross-symbol delete via a mismatched url segment
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
    # / asyncpg execute returns a status string like "DELETE 1" or "DELETE 0"
    if isinstance(result, str) and result.startswith("DELETE "):
        try:
            return int(result.split(" ", 1)[1]) > 0
        except (ValueError, IndexError):
            return False
    return False


async def delete_all_drawings(pool: asyncpg.Pool, symbol: str) -> int:
    # / bulk delete all drawings for a symbol, returns the deleted count
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

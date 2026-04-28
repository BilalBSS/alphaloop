# / chart alert persistence + crud — price-cross alerts fired by orchestrator alert engine
# / rows live in chart_alerts (migration 037); status machine: active -> fired (atomic), active -> disabled
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg
import structlog

from ._serialize import iso as _iso
from ._serialize import whitelist as _whitelist

logger = structlog.get_logger(__name__)

# / direction + status enums
DIRECTION_ABOVE = "above"
DIRECTION_BELOW = "below"
STATUS_ACTIVE = "active"
STATUS_FIRED = "fired"
STATUS_DISABLED = "disabled"

VALID_DIRECTIONS: set[str] = {DIRECTION_ABOVE, DIRECTION_BELOW}
VALID_STATUSES: set[str] = {STATUS_ACTIVE, STATUS_FIRED, STATUS_DISABLED}

# / label length cap matches the varchar(200) column in the migration
_LABEL_MAX = 200

# / whitelist of fields that can be updated via update_alert
_UPDATE_WHITELIST: set[str] = {"price", "direction", "label", "status"}


def sanitize_direction(d: Any) -> str | None:
    return _whitelist(d, VALID_DIRECTIONS)


def sanitize_status(s: Any) -> str | None:
    return _whitelist(s, VALID_STATUSES)


def validate_label(label: Any) -> str | None:
    # / optional, max 200 chars, strip, returns None for empty/non-string/whitespace
    if label is None:
        return None
    if not isinstance(label, str):
        return None
    trimmed = label.strip()
    if not trimmed:
        return None
    if len(trimmed) > _LABEL_MAX:
        trimmed = trimmed[:_LABEL_MAX]
    return trimmed


def _coerce_price(price: Any) -> Decimal | None:
    # / accept int/float/str, reject nan/inf/negative/zero, returns Decimal for pg numeric
    if price is None or isinstance(price, bool):
        return None
    try:
        value = Decimal(str(price))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not value.is_finite():
        return None
    if value <= 0:
        return None
    return value


def _row_to_alert(row: dict) -> dict:
    # / consistent shape for every endpoint — decimal is serialized as float for json
    price = row.get("price")
    return {
        "id": row.get("id"),
        "symbol": row.get("symbol"),
        "price": float(price) if price is not None else None,
        "direction": row.get("direction"),
        "label": row.get("label"),
        "status": row.get("status"),
        "last_check": _iso(row.get("last_check")),
        "fired_at": _iso(row.get("fired_at")),
        "created_at": _iso(row.get("created_at")),
    }


async def list_alerts(
    pool: asyncpg.Pool | None,
    symbol: str | None = None,
    status: str | None = None,
) -> list[dict]:
    # / optional symbol + status filters, newest first so latest alerts render on top
    if pool is None:
        return []
    clauses: list[str] = []
    args: list[Any] = []
    if symbol:
        clauses.append(f"symbol = ${len(args) + 1}")
        args.append(symbol)
    if status:
        clauses.append(f"status = ${len(args) + 1}")
        args.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""SELECT id, symbol, price, direction, label, status,
              last_check, fired_at, created_at
              FROM chart_alerts {where}
              ORDER BY created_at DESC"""
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
    except Exception as exc:
        logger.debug("alerts_list_failed", symbol=symbol, error=str(exc))
        return []
    return [_row_to_alert(dict(r)) for r in rows]


async def get_alert(pool: asyncpg.Pool | None, alert_id: int) -> dict | None:
    # / single row by id, None when missing
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT id, symbol, price, direction, label, status,
                last_check, fired_at, created_at
                FROM chart_alerts WHERE id = $1""",
                alert_id,
            )
    except Exception as exc:
        logger.debug("alerts_get_failed", alert_id=alert_id, error=str(exc))
        return None
    if row is None:
        return None
    return _row_to_alert(dict(row))


async def create_alert(
    pool: asyncpg.Pool | None,
    symbol: str,
    price: Any,
    direction: Any,
    label: Any = None,
) -> dict:
    # / insert a new active alert; validates direction + price; returns {"error": ...} on bad input
    if pool is None:
        return {"error": "db_not_ready"}
    dir_ok = sanitize_direction(direction)
    if dir_ok is None:
        return {"error": "invalid_direction"}
    price_ok = _coerce_price(price)
    if price_ok is None:
        return {"error": "invalid_price"}
    label_ok = validate_label(label)
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO chart_alerts
                (symbol, price, direction, label, status, created_at)
                VALUES ($1, $2, $3, $4, 'active', NOW())
                RETURNING id, symbol, price, direction, label, status,
                last_check, fired_at, created_at""",
                symbol,
                price_ok,
                dir_ok,
                label_ok,
            )
    except Exception as exc:
        logger.debug("alerts_create_failed", symbol=symbol, error=str(exc))
        return {"error": "insert_failed"}
    if row is None:
        return {"error": "insert_failed"}
    return _row_to_alert(dict(row))


async def update_alert(
    pool: asyncpg.Pool | None,
    symbol: str,
    alert_id: int,
    **fields: Any,
) -> dict | None:
    # / partial update, only whitelisted fields; returns None on miss or empty patch
    # / scoped to symbol so a mismatched url segment cannot bleed across symbols
    if pool is None:
        return None
    sets: list[str] = []
    args: list[Any] = []
    for key, value in fields.items():
        if key not in _UPDATE_WHITELIST:
            continue
        if key == "direction":
            clean = sanitize_direction(value)
            if clean is None:
                return None
            value = clean
        elif key == "status":
            clean = sanitize_status(value)
            if clean is None:
                return None
            value = clean
        elif key == "price":
            clean = _coerce_price(value)
            if clean is None:
                return None
            value = clean
        elif key == "label":
            value = validate_label(value)
        sets.append(f"{key} = ${len(args) + 1}")
        args.append(value)
    if not sets:
        return None
    args.append(alert_id)
    args.append(symbol)
    sql = f"""UPDATE chart_alerts SET {', '.join(sets)}
              WHERE id = ${len(args) - 1} AND symbol = ${len(args)}
              RETURNING id, symbol, price, direction, label, status,
              last_check, fired_at, created_at"""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, *args)
    except Exception as exc:
        logger.debug("alerts_update_failed", alert_id=alert_id, error=str(exc))
        return None
    if row is None:
        return None
    return _row_to_alert(dict(row))


async def delete_alert(pool: asyncpg.Pool | None, symbol: str, alert_id: int) -> bool:
    # / hard delete by id scoped to symbol, returns true on hit
    # / scoping prevents a cross-symbol delete via a mismatched url segment
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM chart_alerts WHERE id = $1 AND symbol = $2",
                alert_id,
                symbol,
            )
    except Exception as exc:
        logger.debug("alerts_delete_failed", alert_id=alert_id, error=str(exc))
        return False
    if isinstance(result, str) and result.startswith("DELETE "):
        try:
            return int(result.split(" ", 1)[1]) > 0
        except (ValueError, IndexError):
            return False
    return False


async def mark_fired(
    pool: asyncpg.Pool | None,
    alert_id: int,
    fired_at: Any,
) -> dict | None:
    # / atomic check-and-set guards against double-fire when two ticks race
    # / the WHERE status='active' clause means a second call returns None
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE chart_alerts
                SET status = 'fired', fired_at = $2
                WHERE id = $1 AND status = 'active'
                RETURNING id, symbol, price, direction, label, status,
                last_check, fired_at, created_at""",
                alert_id,
                fired_at,
            )
    except Exception as exc:
        logger.debug("alerts_mark_fired_failed", alert_id=alert_id, error=str(exc))
        return None
    if row is None:
        return None
    return _row_to_alert(dict(row))


async def mark_checked(
    pool: asyncpg.Pool | None,
    alert_ids: list[int],
    checked_at: Any,
) -> None:
    # / batch update last_check across all scanned alerts in a single statement
    if pool is None or not alert_ids:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE chart_alerts SET last_check = $2 WHERE id = ANY($1::bigint[])",
                alert_ids,
                checked_at,
            )
    except Exception as exc:
        logger.debug("alerts_mark_checked_failed", count=len(alert_ids), error=str(exc))

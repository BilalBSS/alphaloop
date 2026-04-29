
from __future__ import annotations

from decimal import Decimal

import structlog

logger = structlog.get_logger(__name__)

_STATUS_TABLES = {"trade_signals", "approved_trades"}


async def store_trade_signal(
    pool, strategy_id: str, symbol: str, signal_type: str,
    strength: float, regime: str | None, details: dict | None = None,
) -> int:
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            """SELECT id, status FROM trade_signals
            WHERE strategy_id = $1 AND symbol = $2 AND signal_type = $3
            AND created_at >= CURRENT_DATE AND created_at < CURRENT_DATE + INTERVAL '1 day'
            ORDER BY created_at DESC LIMIT 1""",
            strategy_id, symbol, signal_type,
        )
        if existing:
            if existing["status"] == "pending":
                await conn.execute(
                    """UPDATE trade_signals SET strength = $1, regime = $2, details = $3
                    WHERE id = $4""",
                    Decimal(str(strength)), regime,
                    details if details else None, existing["id"],
                )
            return existing["id"]
        row = await conn.fetchrow(
            """INSERT INTO trade_signals (strategy_id, symbol, signal_type, strength, regime, details, status)
            VALUES ($1, $2, $3, $4, $5, $6, 'pending')
            RETURNING id""",
            strategy_id, symbol, signal_type,
            Decimal(str(strength)), regime,
            details if details else None,
        )
        return row["id"]


async def fetch_pending_signals(pool, limit: int = 50) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM trade_signals
            WHERE status = 'pending'
            ORDER BY
                CASE WHEN signal_type = 'sell' THEN 0 ELSE 1 END,
                strength DESC NULLS LAST,
                created_at ASC
            LIMIT $1""",
            limit,
        )
    return [dict(r) for r in rows]


async def store_approved_trade(
    pool, signal_id: int, symbol: str, side: str, qty: float,
    order_type: str = "market", strategy_id: str | None = None,
) -> int:
    # / insert approved trade
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO approved_trades (signal_id, symbol, side, qty, order_type, status, strategy_id)
            VALUES ($1, $2, $3, $4, $5, 'pending', $6)
            RETURNING id""",
            signal_id, symbol, side,
            Decimal(str(qty)), order_type, strategy_id,
        )
        return row["id"]


async def fetch_pending_trades(pool, limit: int = 50) -> list[dict]:
    # / unexecuted approved trades
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM approved_trades
            WHERE status = 'pending'
            ORDER BY created_at ASC LIMIT $1""",
            limit,
        )
    return [dict(r) for r in rows]


async def count_today_approved_trades(pool) -> int:
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """SELECT COUNT(*) FROM approved_trades
            WHERE created_at >= CURRENT_DATE AND created_at < CURRENT_DATE + INTERVAL '1 day'""",
        )
        return int(count or 0)


async def count_today_approved_trades_for_strategy(pool, strategy_id: str) -> int:
    # / per-strategy daily counter
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """SELECT COUNT(*) FROM approved_trades
            WHERE strategy_id = $1
            AND created_at >= CURRENT_DATE
            AND created_at < CURRENT_DATE + INTERVAL '1 day'""",
            strategy_id,
        )
        return int(count or 0)


async def count_pending_signals_for_strategy(pool, strategy_id: str) -> int:
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """SELECT COUNT(*) FROM trade_signals
            WHERE strategy_id = $1
            AND status = 'pending'
            AND created_at >= NOW() - INTERVAL '10 minutes'""",
            strategy_id,
        )
        return int(count or 0)


async def update_trade_status(
    pool, table: str, row_id: int, status: str,
    rejection_reason: str | None = None,
) -> bool:
    if table not in _STATUS_TABLES:
        raise ValueError(f"invalid table '{table}', must be one of {_STATUS_TABLES}")
    async with pool.acquire() as conn:
        if table == "trade_signals" and rejection_reason is not None:
            result = await conn.execute(
                "UPDATE trade_signals SET status = $1, rejection_reason = $2 WHERE id = $3",
                status, rejection_reason[:80], row_id,
            )
        else:
            result = await conn.execute(
                f"UPDATE {table} SET status = $1 WHERE id = $2",
                status, row_id,
            )
        return result == "UPDATE 1"


async def store_trade_log(
    pool, trade_id: int | None, symbol: str, side: str, qty: float,
    price: float, order_id: str | None, broker: str | None,
    regime: str | None, pnl: float | None,
    strategy_id: str | None = None, details: dict | None = None,
) -> int:
    # / log executed trade
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO trade_log (trade_id, symbol, side, qty, price, order_id, broker, regime, pnl, strategy_id, details)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING id""",
            trade_id, symbol, side,
            Decimal(str(qty)), Decimal(str(price)),
            order_id, broker, regime,
            Decimal(str(pnl)) if pnl is not None else None,
            strategy_id,
            details if details else None,
        )
        return row["id"]


async def fetch_pending_signal_by_id(pool, signal_id: int) -> dict | None:
    # / risk_agent re-validates before approval
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM trade_signals WHERE id = $1 AND status = 'pending'",
            signal_id,
        )
    return dict(row) if row else None


async def fetch_approved_trade_by_id(pool, trade_id: int) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM approved_trades WHERE id = $1", trade_id,
        )
    return dict(row) if row else None


async def claim_approved_trade_atomic(pool, trade_id: int) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE approved_trades SET status = 'executing' WHERE id = $1 AND status = 'pending'",
            trade_id,
        )
    return result == "UPDATE 1"


async def attach_broker_order_id(pool, trade_id: int, order_id: str) -> None:
    if not order_id:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE approved_trades SET order_id = $1 WHERE id = $2",
                order_id, trade_id,
            )
    except Exception as exc:
        logger.debug("attach_broker_order_id_failed",
                     trade_id=trade_id, error=str(exc)[:120])


async def fetch_strategy_id_by_order(pool, order_id: str) -> tuple[int | None, str | None]:
    if not order_id:
        return None, None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, strategy_id FROM approved_trades WHERE order_id = $1",
                order_id,
            )
    except Exception as exc:
        logger.debug("fetch_strategy_id_by_order_failed",
                     order_id=order_id, error=str(exc)[:120])
        return None, None
    if not row:
        return None, None
    return row.get("id"), row.get("strategy_id")


from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import structlog

logger = structlog.get_logger(__name__)

_sync_skipped_orders = 0


def get_sync_skipped_orders() -> int:
    return _sync_skipped_orders


async def sync_trades_from_alpaca(pool) -> int:
    from src.data.alpaca_client import alpaca_base_url, alpaca_headers, get_alpaca_client
    base = alpaca_base_url()
    headers = alpaca_headers()
    try:
        client = await get_alpaca_client()
        resp = await client.get(
            f"{base}/v2/orders",
            headers=headers,
            params={"status": "filled", "limit": 200, "direction": "desc"},
        )
        resp.raise_for_status()
        orders = resp.json()
    except Exception as exc:
        logger.warning("alpaca_sync_fetch_failed", error=str(exc))
        return 0

    order_ids = [o["id"] for o in orders]
    synced = 0
    async with pool.acquire() as conn:
        existing = await conn.fetch(
            "SELECT order_id FROM trade_log WHERE order_id = ANY($1)",
            order_ids,
        )
        existing_set = {r["order_id"] for r in existing}

        for o in orders:
            order_id = o["id"]
            if order_id in existing_set:
                continue
            symbol = o["symbol"]
            side = o["side"]
            qty = float(o.get("filled_qty", 0))
            price = float(o.get("filled_avg_price", 0))
            filled_at_raw = o.get("filled_at")
            filled_at: datetime | None = None
            if filled_at_raw:
                try:
                    filled_at = datetime.fromisoformat(
                        str(filled_at_raw).replace("Z", "+00:00"),
                    )
                except (ValueError, TypeError):
                    global _sync_skipped_orders
                    _sync_skipped_orders += 1
                    logger.warning("alpaca_sync_bad_filled_at",
                                   order_id=order_id,
                                   raw=str(filled_at_raw)[:40],
                                   skipped_total=_sync_skipped_orders)
                    continue
            if qty <= 0 or price <= 0 or filled_at is None:
                continue
            pnl: Decimal | None = None
            if side == "sell":
                prior_buy = await conn.fetchrow(
                    """SELECT price FROM trade_log
                    WHERE symbol = $1 AND side = 'buy' AND created_at < $2::timestamptz
                    ORDER BY created_at DESC LIMIT 1""",
                    symbol, filled_at,
                )
                if prior_buy and prior_buy["price"]:
                    pnl = (Decimal(str(price)) - prior_buy["price"]) * Decimal(str(qty))
            approved_row = await conn.fetchrow(
                "SELECT id, strategy_id FROM approved_trades WHERE order_id = $1",
                order_id,
            )
            linked_trade_id = approved_row["id"] if approved_row else None
            linked_strategy_id = approved_row["strategy_id"] if approved_row else None
            await conn.execute(
                """INSERT INTO trade_log
                (trade_id, symbol, side, qty, price, order_id, broker, regime, pnl, strategy_id, details, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, 'AlpacaBroker', NULL, $7, $8,
                        $9, $10::timestamptz)""",
                linked_trade_id,
                symbol, side, Decimal(str(qty)), Decimal(str(price)),
                order_id, pnl, linked_strategy_id,
                {"order_type": o.get("type"), "time_in_force": o.get("time_in_force")},
                filled_at,
            )
            position_strategy = linked_strategy_id or "untracked"
            skip_insert = False
            if position_strategy == "untracked":
                owned_by_real = await conn.fetchrow(
                    """SELECT 1 FROM strategy_positions
                    WHERE symbol = $1 AND strategy_id <> 'untracked' AND qty > 0
                    LIMIT 1""",
                    symbol,
                )
                skip_insert = bool(owned_by_real)
            if not skip_insert:
                if side == "buy":
                    await conn.execute(
                        """
                        INSERT INTO strategy_positions (strategy_id, symbol, qty, avg_entry_price, updated_at)
                        VALUES ($1, $2, $3, $4, $5::timestamptz)
                        ON CONFLICT (strategy_id, symbol) DO UPDATE SET
                            avg_entry_price = (
                                (strategy_positions.avg_entry_price * strategy_positions.qty
                                 + EXCLUDED.avg_entry_price * EXCLUDED.qty)
                                / NULLIF(strategy_positions.qty + EXCLUDED.qty, 0)
                            ),
                            qty = strategy_positions.qty + EXCLUDED.qty,
                            updated_at = EXCLUDED.updated_at
                        """,
                        position_strategy, symbol, Decimal(str(qty)), Decimal(str(price)), filled_at,
                    )
                elif side == "sell":
                    await conn.execute(
                        """
                        UPDATE strategy_positions
                        SET qty = qty - $2, updated_at = $3::timestamptz
                        WHERE strategy_id = $4 AND symbol = $1
                        """,
                        symbol, Decimal(str(qty)), filled_at, position_strategy,
                    )
                    await conn.execute(
                        "DELETE FROM strategy_positions WHERE strategy_id = $1 AND symbol = $2 AND qty <= 0",
                        position_strategy, symbol,
                    )
            synced += 1
    if synced:
        logger.info("alpaca_sync_complete", synced=synced, total_orders=len(orders))
    return synced


async def backfill_trade_pnl(pool) -> int:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, symbol, qty, price, created_at FROM trade_log
            WHERE side = 'sell' AND pnl IS NULL
            ORDER BY created_at ASC""",
        )
        updated = 0
        for r in rows:
            prior = await conn.fetchrow(
                """SELECT price FROM trade_log
                WHERE symbol = $1 AND side = 'buy' AND created_at < $2
                ORDER BY created_at DESC LIMIT 1""",
                r["symbol"], r["created_at"],
            )
            if prior and prior["price"]:
                pnl = (float(r["price"]) - float(prior["price"])) * float(r["qty"])
                await conn.execute(
                    "UPDATE trade_log SET pnl = $1 WHERE id = $2",
                    Decimal(str(pnl)), r["id"],
                )
                updated += 1
    if updated:
        logger.info("trade_pnl_backfilled", updated=updated)
    return updated

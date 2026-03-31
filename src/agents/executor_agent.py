# / executor agent — places orders for approved trades
# / logs results to trade_log, updates approved_trades status
# / guards against double execution

from __future__ import annotations

import structlog

from src.agents import tools
from src.brokers.base import BrokerInterface
from src.notifications.notifier import notify_trade_executed, notify_trade_error

logger = structlog.get_logger(__name__)


class ExecutorAgent:

    async def execute_trade(
        self, pool, trade_id: int, broker: BrokerInterface,
    ) -> dict:
        # / place order for one approved trade
        # / fetch approved trade
        async with pool.acquire() as conn:
            trade = await conn.fetchrow(
                "SELECT * FROM approved_trades WHERE id = $1", trade_id,
            )
        if not trade:
            return {"status": "error", "reason": "trade_not_found"}

        trade = dict(trade)

        # / atomic guard against double execution — WHERE status = 'pending'
        # / prevents toctou race between check and update
        async with pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE approved_trades SET status = 'executing' WHERE id = $1 AND status = 'pending'",
                trade_id,
            )
        if result != "UPDATE 1":
            logger.warning(
                "executor_skip_non_pending",
                trade_id=trade_id, status=trade["status"],
            )
            return {"status": "skipped", "reason": f"status_is_{trade['status']}"}

        symbol = trade["symbol"]
        side = trade["side"]
        qty = float(trade["qty"])
        order_type = trade.get("order_type", "market")
        strategy_id = trade.get("strategy_id")

        try:
            order = await broker.place_order(
                symbol=symbol, qty=qty, side=side, order_type=order_type,
            )
        except Exception as exc:
            logger.error(
                "executor_order_failed",
                trade_id=trade_id, symbol=symbol, error=str(exc),
            )
            await tools.update_trade_status(pool, "approved_trades", trade_id, "error")
            notify_trade_error(symbol, side, str(exc))
            return {"status": "error", "reason": str(exc)}

        if order.status == "filled":
            # / fetch regime for logging
            regime = await tools.fetch_latest_regime(pool, "equity")

            # / track strategy-level position
            pnl = None
            if side == "buy" and strategy_id:
                await tools.open_strategy_position(
                    pool, strategy_id, symbol, order.filled_qty, order.filled_price or 0.0,
                )
            elif side == "sell" and strategy_id:
                entry_price = await tools.close_strategy_position(
                    pool, strategy_id, symbol, order.filled_qty,
                )
                if entry_price and order.filled_price:
                    pnl = (order.filled_price - entry_price) * order.filled_qty

            log_id = await tools.store_trade_log(
                pool,
                trade_id=trade_id,
                symbol=symbol,
                side=side,
                qty=order.filled_qty,
                price=order.filled_price or 0.0,
                order_id=order.order_id,
                broker=type(broker).__name__,
                regime=regime,
                pnl=pnl,
                strategy_id=strategy_id,
                details={
                    "order_status": order.status,
                    "order_type": order_type,
                },
            )
            await tools.update_trade_status(pool, "approved_trades", trade_id, "filled")

            notify_trade_executed(symbol, side, order.filled_qty, order.filled_price or 0, strategy_id)
            logger.info(
                "trade_executed",
                trade_id=trade_id, log_id=log_id,
                symbol=symbol, side=side,
                qty=order.filled_qty, price=order.filled_price,
            )
            return {
                "status": "filled",
                "log_id": log_id,
                "order_id": order.order_id,
                "qty": order.filled_qty,
                "price": order.filled_price,
            }

        elif order.status in ("rejected", "cancelled"):
            await tools.update_trade_status(pool, "approved_trades", trade_id, "failed")
            logger.warning(
                "trade_rejected_by_broker",
                trade_id=trade_id, symbol=symbol,
                broker_status=order.status, details=order.details,
            )
            return {"status": "failed", "reason": order.status, "details": order.details}

        else:
            # / alpaca market orders fill within seconds — poll for fill
            import asyncio
            for _ in range(10):
                await asyncio.sleep(1)
                try:
                    updated = await broker.get_order_status(order.order_id)
                    if updated.status == "filled":
                        order = updated
                        break
                    elif updated.status in ("rejected", "cancelled"):
                        await tools.update_trade_status(pool, "approved_trades", trade_id, "failed")
                        return {"status": "failed", "reason": updated.status}
                except Exception:
                    pass

            if order.status == "filled":
                regime = await tools.fetch_latest_regime(pool, "equity")

                # / track strategy-level position (polled fill)
                pnl = None
                if side == "buy" and strategy_id:
                    await tools.open_strategy_position(
                        pool, strategy_id, symbol, order.filled_qty, order.filled_price or 0.0,
                    )
                elif side == "sell" and strategy_id:
                    entry_price = await tools.close_strategy_position(
                        pool, strategy_id, symbol, order.filled_qty,
                    )
                    if entry_price and order.filled_price:
                        pnl = (order.filled_price - entry_price) * order.filled_qty

                log_id = await tools.store_trade_log(
                    pool, trade_id=trade_id, symbol=symbol, side=side,
                    qty=order.filled_qty, price=order.filled_price or 0.0,
                    order_id=order.order_id, broker=type(broker).__name__,
                    regime=regime, pnl=pnl, strategy_id=strategy_id,
                    details={"order_status": "filled", "order_type": order_type},
                )
                await tools.update_trade_status(pool, "approved_trades", trade_id, "filled")
                notify_trade_executed(symbol, side, order.filled_qty, order.filled_price or 0, strategy_id)
                logger.info("trade_executed_after_poll", trade_id=trade_id, log_id=log_id,
                            symbol=symbol, side=side, qty=order.filled_qty, price=order.filled_price)
                return {"status": "filled", "log_id": log_id, "order_id": order.order_id,
                        "qty": order.filled_qty, "price": order.filled_price}

            # / still not filled after 10s — log current status
            await tools.update_trade_status(pool, "approved_trades", trade_id, order.status)
            logger.warning("trade_not_filled_after_poll", trade_id=trade_id, symbol=symbol, status=order.status)
            return {"status": order.status}

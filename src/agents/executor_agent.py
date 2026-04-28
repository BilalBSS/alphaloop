# / executor agent — places orders for approved trades
# / logs results to trade_log, updates approved_trades status
# / guards against double execution

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog

from src.agents import tools
from src.brokers.base import BrokerInterface
from src.data.symbols import is_crypto
from src.notifications.notifier import notify_trade_error, notify_trade_executed

logger = structlog.get_logger(__name__)

# / strong refs so event loop doesn't gc background post-mortem tasks
_POST_MORTEM_TASKS: set = set()


def _broadcast_fill(symbol: str, side: str, qty: float, price: float,
                    strategy_id: str | None, log_id: int | None,
                    pnl: float | None) -> None:
    # / phase 7 tier 1: fan trade_executed + position_update out to ws clients.
    # / late-bind the dashboard import so tests / headless workers don't fail.
    try:
        from src.dashboard.app import _ws_clients, broadcast
    except Exception:
        return
    if not _ws_clients:
        return
    try:
        tools.fire_and_forget(broadcast("trade_executed", {
            "symbol": symbol, "side": side, "qty": float(qty),
            "price": float(price) if price else 0.0,
            "strategy_id": strategy_id, "log_id": log_id,
            "pnl": float(pnl) if pnl is not None else None,
        }))
        tools.fire_and_forget(broadcast("position_update", {"symbol": symbol}))
    except Exception as exc:
        logger.debug("broadcast_fill_failed", error=str(exc)[:120])


def _should_trigger_post_mortem(pnl: float | None, entry_notional: float) -> bool:
    # / phase 2: loss > $50 OR loss > 2% of entry notional
    if pnl is None or pnl >= 0:
        return False
    try:
        pnl_abs = float(os.environ.get("POST_MORTEM_PNL_ABS", "50"))
    except (TypeError, ValueError):
        pnl_abs = 50.0
    try:
        pnl_pct = float(os.environ.get("POST_MORTEM_PNL_PCT", "0.02"))
    except (TypeError, ValueError):
        pnl_pct = 0.02
    if abs(pnl) > pnl_abs:
        return True
    return entry_notional > 0 and abs(pnl) / entry_notional > pnl_pct


def _spawn_post_mortem(
    pool, trade_id: int | None, strategy_id: str | None,
    symbol: str, pnl: float, trigger_type: str,
) -> None:
    # / fire-and-forget launcher — cooldown enforced inside write_post_mortem
    if not strategy_id or pnl is None:
        return
    try:
        from src.knowledge.post_mortem_writer import write_post_mortem
        task = asyncio.create_task(
            write_post_mortem(
                pool=pool,
                trade_id=trade_id,
                strategy_id=strategy_id,
                symbol=symbol,
                pnl=float(pnl),
                trigger_type=trigger_type,
            )
        )
        _POST_MORTEM_TASKS.add(task)
        task.add_done_callback(_POST_MORTEM_TASKS.discard)
    except RuntimeError:
        # / no running loop — skip
        pass
    except Exception as exc:
        logger.info("post_mortem_spawn_failed", error=str(exc)[:120])


def _strategy_killed_on_disk(strategy_id: str) -> bool:
    # / bug e: disk fallback for kill gate — defends against stale/missing in-memory pool
    # / reads the strategy config json directly to see the authoritative status
    import json as _json
    import re as _re
    from pathlib import Path as _Path
    if not strategy_id or not _re.match(r"^[a-zA-Z0-9_-]+$", strategy_id):
        return False
    path = _Path(__file__).parent.parent.parent / "configs" / "strategies" / f"{strategy_id}.json"
    try:
        with open(path) as f:
            cfg = _json.load(f)
        return (cfg.get("metadata") or {}).get("status") == "killed"
    except Exception:
        return False


class ExecutorAgent:

    async def execute_trade(
        self, pool, trade_id: int, broker: BrokerInterface, strategy_pool=None,
    ) -> dict:
        # / place order for one approved trade
        trade = await tools.fetch_approved_trade_by_id(pool, trade_id)
        if not trade:
            return {"status": "error", "reason": "trade_not_found"}
        strategy_id = trade.get("strategy_id")

        # / killed-strategy gate: reject trades from strategies marked killed in the pool
        # / prevents orphaned approved_trades from executing after evolution kills a strategy
        # / status value must fit VARCHAR(20) on approved_trades.status
        # / bug e: also check on-disk config when in-memory pool is missing/stale
        if strategy_id:
            killed = False
            if strategy_pool is not None:
                entry = strategy_pool.get(strategy_id)
                if entry is not None and entry.status == "killed":
                    killed = True
            # / disk fallback: authoritative source when pool is None or entry missing
            if not killed:
                try:
                    killed = _strategy_killed_on_disk(strategy_id)
                except Exception:
                    killed = False
            if killed:
                await tools.update_trade_status(pool, "approved_trades", trade_id, "killed_strategy")
                logger.warning(
                    "executor_rejected_killed_strategy",
                    trade_id=trade_id, strategy_id=strategy_id, symbol=trade["symbol"],
                )
                return {"status": "cancelled", "reason": f"strategy_{strategy_id}_killed"}

        # / atomic guard against double execution — prevents toctou race
        claimed = await tools.claim_approved_trade_atomic(pool, trade_id)
        if not claimed:
            logger.warning(
                "executor_skip_non_pending",
                trade_id=trade_id, status=trade["status"],
            )
            return {"status": "skipped", "reason": f"status_is_{trade['status']}"}

        symbol = trade["symbol"]
        side = trade["side"]
        qty = float(trade["qty"])
        order_type = trade.get("order_type", "market")

        # / use extended hours for stock market orders outside regular hours
        ext_hours = False
        if not is_crypto(symbol) and order_type == "market":
            now_et = datetime.now(ZoneInfo("America/New_York"))
            mins = now_et.hour * 60 + now_et.minute
            ext_hours = now_et.weekday() >= 5 or mins < 570 or mins >= 960

        try:
            order = await broker.place_order(
                symbol=symbol, qty=qty, side=side, order_type=order_type,
                extended_hours=ext_hours,
            )
        except Exception as exc:
            logger.error(
                "executor_order_failed",
                trade_id=trade_id, symbol=symbol, error=str(exc),
            )
            await tools.update_trade_status(pool, "approved_trades", trade_id, "error")
            notify_trade_error(symbol, side, str(exc))
            return {"status": "error", "reason": str(exc)}

        # / persist broker order_id so alpaca_sync can recover strategy_id
        # / on reconciled fills (the usual cause of null strategy_id rows).
        if getattr(order, "order_id", None):
            await tools.attach_broker_order_id(pool, trade_id, order.order_id)

        if order.status == "filled":
            if order.filled_price is None or float(order.filled_price) <= 0:
                await tools.update_trade_status(pool, "approved_trades", trade_id, "pending_reconcile")
                await tools.log_event(
                    pool, level="error", source="executor",
                    message="fill_missing_price",
                    symbol=symbol,
                    details={
                        "trade_id": trade_id, "order_id": order.order_id,
                        "filled_qty": float(order.filled_qty) if order.filled_qty is not None else None,
                        "filled_price": order.filled_price,
                    },
                )
                logger.error(
                    "executor_fill_missing_price",
                    trade_id=trade_id, order_id=order.order_id,
                    symbol=symbol, side=side,
                )
                return {"status": "failed", "reason": "fill_missing_price"}

            # / fetch regime for logging
            regime = await tools.fetch_latest_regime(pool, "equity")

            # / track strategy-level position
            pnl = None
            if side == "buy" and strategy_id:
                await tools.open_strategy_position(
                    pool, strategy_id, symbol, order.filled_qty, order.filled_price,
                )
            elif side == "sell":
                # / bug e: try strategy close first, fall back to most recent buy lookup
                entry_price = None
                if strategy_id:
                    entry_price = await tools.close_strategy_position(
                        pool, strategy_id, symbol, order.filled_qty,
                    )
                if entry_price is None:
                    fallback = await tools.fetch_most_recent_open_entry(pool, symbol)
                    if fallback and fallback.get("entry_price") is not None:
                        entry_price = fallback["entry_price"]
                    else:
                        logger.warning("sell_without_entry_history", symbol=symbol, qty=order.filled_qty)
                if entry_price is not None:
                    pnl = (order.filled_price - entry_price) * order.filled_qty

            log_id = await tools.store_trade_log(
                pool,
                trade_id=trade_id,
                symbol=symbol,
                side=side,
                qty=order.filled_qty,
                price=order.filled_price,
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

            notify_trade_executed(symbol, side, order.filled_qty, order.filled_price, strategy_id, pnl=pnl)
            _broadcast_fill(symbol, side, order.filled_qty, order.filled_price,
                            strategy_id, log_id, pnl)
            logger.info(
                "trade_executed",
                trade_id=trade_id, log_id=log_id,
                symbol=symbol, side=side,
                qty=order.filled_qty, price=order.filled_price,
            )

            # / phase 2: trigger post-mortem on loss-close (sell branch only)
            if side == "sell" and pnl is not None:
                entry_notional = 0.0
                if 'entry_price' in locals() and entry_price is not None:
                    entry_notional = float(entry_price) * float(order.filled_qty)
                if _should_trigger_post_mortem(pnl, entry_notional):
                    _spawn_post_mortem(
                        pool, log_id, strategy_id, symbol, pnl, "loss_threshold",
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
                if order.filled_price is None or float(order.filled_price) <= 0:
                    await tools.update_trade_status(pool, "approved_trades", trade_id, "pending_reconcile")
                    await tools.log_event(
                        pool, level="error", source="executor",
                        message="fill_missing_price",
                        symbol=symbol,
                        details={
                            "trade_id": trade_id, "order_id": order.order_id,
                            "filled_qty": float(order.filled_qty) if order.filled_qty is not None else None,
                            "filled_price": order.filled_price,
                            "polled": True,
                        },
                    )
                    logger.error(
                        "executor_fill_missing_price",
                        trade_id=trade_id, order_id=order.order_id,
                        symbol=symbol, side=side, polled=True,
                    )
                    return {"status": "failed", "reason": "fill_missing_price"}

                regime = await tools.fetch_latest_regime(pool, "equity")

                # / track strategy-level position (polled fill)
                pnl = None
                if side == "buy" and strategy_id:
                    await tools.open_strategy_position(
                        pool, strategy_id, symbol, order.filled_qty, order.filled_price,
                    )
                elif side == "sell":
                    # / bug e: try strategy close first, fall back to most recent buy lookup
                    entry_price = None
                    if strategy_id:
                        entry_price = await tools.close_strategy_position(
                            pool, strategy_id, symbol, order.filled_qty,
                        )
                    if entry_price is None:
                        fallback = await tools.fetch_most_recent_open_entry(pool, symbol)
                        if fallback and fallback.get("entry_price") is not None:
                            entry_price = fallback["entry_price"]
                        else:
                            logger.warning("sell_without_entry_history", symbol=symbol, qty=order.filled_qty)
                    if entry_price is not None:
                        pnl = (order.filled_price - entry_price) * order.filled_qty

                log_id = await tools.store_trade_log(
                    pool, trade_id=trade_id, symbol=symbol, side=side,
                    qty=order.filled_qty, price=order.filled_price,
                    order_id=order.order_id, broker=type(broker).__name__,
                    regime=regime, pnl=pnl, strategy_id=strategy_id,
                    details={"order_status": "filled", "order_type": order_type},
                )
                await tools.update_trade_status(pool, "approved_trades", trade_id, "filled")
                notify_trade_executed(symbol, side, order.filled_qty, order.filled_price, strategy_id, pnl=pnl)
                _broadcast_fill(symbol, side, order.filled_qty, order.filled_price,
                                strategy_id, log_id, pnl)
                logger.info("trade_executed_after_poll", trade_id=trade_id, log_id=log_id,
                            symbol=symbol, side=side, qty=order.filled_qty, price=order.filled_price)

                # / phase 2: trigger post-mortem on loss-close (polled fill)
                if side == "sell" and pnl is not None:
                    entry_notional = 0.0
                    if 'entry_price' in locals() and entry_price is not None:
                        entry_notional = float(entry_price) * float(order.filled_qty)
                    if _should_trigger_post_mortem(pnl, entry_notional):
                        _spawn_post_mortem(
                            pool, log_id, strategy_id, symbol, pnl, "loss_threshold",
                        )
                return {"status": "filled", "log_id": log_id, "order_id": order.order_id,
                        "qty": order.filled_qty, "price": order.filled_price}

            # / still not filled after 10s — log current status
            await tools.update_trade_status(pool, "approved_trades", trade_id, order.status)
            logger.warning("trade_not_filled_after_poll", trade_id=trade_id, symbol=symbol, status=order.status)
            return {"status": order.status}

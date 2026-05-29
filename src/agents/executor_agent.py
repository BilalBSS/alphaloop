# / guards against double execution

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from src.agents.data_tools import fire_and_forget, log_event
from src.agents.market_tools import fetch_latest_regime
from src.agents.position_tools import (
    close_strategy_position,
    fetch_most_recent_open_entry,
    open_strategy_position,
)
from src.agents.task_tracker import ExecutorTaskTracker
from src.agents.trade_tools import (
    attach_broker_order_id,
    claim_approved_trade_atomic,
    fetch_approved_trade_by_id,
    store_trade_log,
    update_trade_status,
)
from src.brokers.base import BrokerInterface
from src.data.symbols import is_crypto
from src.notifications.notifier import notify_trade_error, notify_trade_executed

logger = structlog.get_logger(__name__)


def _broadcast_fill(symbol: str, side: str, qty: float, price: float,
                    strategy_id: str | None, log_id: int | None,
                    pnl: float | None) -> None:
    try:
        from src.dashboard.app import _ws_clients, broadcast
    except ImportError:
        return
    if not _ws_clients:
        return
    try:
        fire_and_forget(broadcast("trade_executed", {
            "symbol": symbol, "side": side, "qty": float(qty),
            "price": float(price) if price else 0.0,
            "strategy_id": strategy_id, "log_id": log_id,
            "pnl": float(pnl) if pnl is not None else None,
        }))
        fire_and_forget(broadcast("position_update", {"symbol": symbol}))
    except Exception as exc:
        logger.debug("broadcast_fill_failed", error=str(exc)[:120])


def _post_mortem_trigger(pnl: float | None, entry_notional: float) -> str | None:
    if pnl is None:
        return None
    try:
        loss_pct = float(os.environ.get("POST_MORTEM_PNL_PCT", "0.01"))
    except (TypeError, ValueError):
        loss_pct = 0.01
    try:
        loss_abs = float(os.environ.get("POST_MORTEM_PNL_ABS", "10"))
    except (TypeError, ValueError):
        loss_abs = 10.0
    try:
        win_pct = float(os.environ.get("POST_MORTEM_WIN_PCT", "0.02"))
    except (TypeError, ValueError):
        win_pct = 0.02
    try:
        win_abs = float(os.environ.get("POST_MORTEM_WIN_ABS", "20"))
    except (TypeError, ValueError):
        win_abs = 20.0

    pct_of_notional = abs(pnl) / entry_notional if entry_notional > 0 else 0.0
    if pnl < 0 and (abs(pnl) > loss_abs or pct_of_notional > loss_pct):
        return "loss_threshold"
    if pnl > 0 and (pnl > win_abs or pct_of_notional > win_pct):
        return "win_threshold"
    return None


def _spawn_post_mortem(
    tracker: ExecutorTaskTracker, pool,
    trade_id: int | None, strategy_id: str | None,
    symbol: str, pnl: float, trigger_type: str,
) -> None:
    if not strategy_id or pnl is None:
        return
    try:
        from src.knowledge.post_mortem_writer import write_post_mortem
        tracker.spawn(write_post_mortem(
            pool=pool,
            trade_id=trade_id,
            strategy_id=strategy_id,
            symbol=symbol,
            pnl=float(pnl),
            trigger_type=trigger_type,
        ))
    except Exception as exc:
        logger.info("post_mortem_spawn_failed", error=str(exc)[:120])


def _strategy_killed_on_disk(strategy_id: str) -> bool:
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
    except (OSError, _json.JSONDecodeError, AttributeError):
        return False


def _compute_extended_hours(symbol: str, order_type: str) -> bool:
    if is_crypto(symbol) or order_type != "market":
        return False
    now_et = datetime.now(ZoneInfo("America/New_York"))
    mins = now_et.hour * 60 + now_et.minute
    return now_et.weekday() >= 5 or mins < 570 or mins >= 960


class ExecutorAgent:

    def __init__(self) -> None:
        self.tasks = ExecutorTaskTracker()
        # / tracked stop orders
        self._protective_stops: dict[tuple[str, str], str] = {}

    async def execute_trade(
        self, pool, trade_id: int, broker: BrokerInterface, strategy_pool=None,
    ) -> dict:
        trade = await fetch_approved_trade_by_id(pool, trade_id)
        if not trade:
            return {"status": "error", "reason": "trade_not_found"}

        gate = await self._preflight(pool, trade_id, trade, strategy_pool)
        if gate is not None:
            return gate

        symbol = trade["symbol"]
        side = trade["side"]
        qty = float(trade["qty"])
        order_type = trade.get("order_type", "market")
        strategy_id = trade.get("strategy_id")

        # / submit order to broker
        try:
            order = await broker.place_order(
                symbol=symbol, qty=qty, side=side, order_type=order_type,
                extended_hours=_compute_extended_hours(symbol, order_type),
            )
        except Exception as exc:
            logger.error(
                "executor_order_failed",
                trade_id=trade_id, symbol=symbol, error=str(exc),
            )
            await update_trade_status(pool, "approved_trades", trade_id, "error")
            notify_trade_error(symbol, side, str(exc))
            return {"status": "error", "reason": str(exc)}

        if getattr(order, "order_id", None):
            await attach_broker_order_id(pool, trade_id, order.order_id)
        else:
            logger.warning("executor_no_order_id",
                           trade_id=trade_id, symbol=symbol, side=side,
                           broker=type(broker).__name__)

        if order.status == "filled":
            return await self._handle_fill(
                pool, trade_id, broker, order, trade, strategy_id,
                polled=False, strategy_pool=strategy_pool,
            )

        if order.status in ("rejected", "cancelled"):
            await update_trade_status(pool, "approved_trades", trade_id, "failed")
            logger.warning(
                "trade_rejected_by_broker",
                trade_id=trade_id, symbol=symbol,
                broker_status=order.status, details=order.details,
            )
            return {"status": "failed", "reason": order.status, "details": order.details}

        # / pending: poll for fill
        return await self._poll_and_handle(
            pool, trade_id, broker, order, trade, strategy_id, strategy_pool,
        )

    async def _preflight(
        self, pool, trade_id: int, trade: dict, strategy_pool,
    ) -> dict | None:
        from src.agents.system_flags import is_executor_paused
        if await is_executor_paused(pool):
            logger.warning(
                "executor_paused_skip",
                trade_id=trade_id, symbol=trade.get("symbol"),
            )
            return {"status": "paused", "reason": "executor_paused"}

        strategy_id = trade.get("strategy_id")
        if strategy_id:
            killed = False
            if strategy_pool is not None:
                entry = strategy_pool.get(strategy_id)
                if entry is not None and entry.status == "killed":
                    killed = True
            if not killed:
                try:
                    killed = _strategy_killed_on_disk(strategy_id)
                except OSError:
                    killed = False
            if killed:
                await update_trade_status(pool, "approved_trades", trade_id, "killed_strategy")
                logger.warning(
                    "executor_rejected_killed_strategy",
                    trade_id=trade_id, strategy_id=strategy_id, symbol=trade["symbol"],
                )
                return {"status": "cancelled", "reason": f"strategy_{strategy_id}_killed"}

        claimed = await claim_approved_trade_atomic(pool, trade_id)
        if not claimed:
            logger.warning(
                "executor_skip_non_pending",
                trade_id=trade_id, status=trade["status"],
            )
            return {"status": "skipped", "reason": f"status_is_{trade['status']}"}
        return None

    async def _poll_and_handle(
        self, pool, trade_id: int, broker: BrokerInterface,
        order: Any, trade: dict, strategy_id: str | None, strategy_pool=None,
    ) -> dict:
        for _ in range(10):
            await asyncio.sleep(1)
            try:
                updated = await broker.get_order_status(order.order_id)
                if updated.status == "filled":
                    order = updated
                    break
                if updated.status in ("rejected", "cancelled"):
                    await update_trade_status(pool, "approved_trades", trade_id, "failed")
                    return {"status": "failed", "reason": updated.status}
            except Exception as exc:
                # / swallow poll-tick failure
                logger.debug("order_poll_tick_failed", trade_id=trade_id, error=str(exc)[:120])

        if order.status == "filled":
            return await self._handle_fill(
                pool, trade_id, broker, order, trade, strategy_id,
                polled=True, strategy_pool=strategy_pool,
            )

        await update_trade_status(pool, "approved_trades", trade_id, order.status)
        logger.warning(
            "trade_not_filled_after_poll",
            trade_id=trade_id, symbol=trade["symbol"], status=order.status,
        )
        return {"status": order.status}

    async def _handle_fill(
        self, pool, trade_id: int, broker: BrokerInterface,
        order: Any, trade: dict, strategy_id: str | None,
        polled: bool, strategy_pool=None,
    ) -> dict:
        symbol = trade["symbol"]
        side = trade["side"]
        order_type = trade.get("order_type", "market")

        if order.filled_price is None or float(order.filled_price) <= 0:
            await update_trade_status(pool, "approved_trades", trade_id, "pending_reconcile")
            await log_event(
                pool, level="error", source="executor",
                message="fill_missing_price",
                symbol=symbol,
                details={
                    "trade_id": trade_id, "order_id": order.order_id,
                    "filled_qty": float(order.filled_qty) if order.filled_qty is not None else None,
                    "filled_price": order.filled_price,
                    **({"polled": True} if polled else {}),
                },
            )
            logger.error(
                "executor_fill_missing_price",
                trade_id=trade_id, order_id=order.order_id,
                symbol=symbol, side=side,
                **({"polled": True} if polled else {}),
            )
            return {"status": "failed", "reason": "fill_missing_price"}

        regime = await fetch_latest_regime(pool, "equity")

        # / cancel stop on close
        if side == "sell":
            await self._cancel_protective_stop(broker, strategy_id, symbol)

        pnl, entry_price = await self._update_position_and_pnl(
            pool, side, strategy_id, symbol, order,
        )

        log_id = await store_trade_log(
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
                "order_status": order.status if not polled else "filled",
                "order_type": order_type,
            },
            decision_id=trade.get("decision_id"),
        )
        await update_trade_status(pool, "approved_trades", trade_id, "filled")

        notify_trade_executed(symbol, side, order.filled_qty, order.filled_price, strategy_id, pnl=pnl)
        _broadcast_fill(symbol, side, order.filled_qty, order.filled_price,
                        strategy_id, log_id, pnl)
        logger.info(
            "trade_executed_after_poll" if polled else "trade_executed",
            trade_id=trade_id, log_id=log_id,
            symbol=symbol, side=side,
            qty=order.filled_qty, price=order.filled_price,
        )

        # / protective stop on open
        if side == "buy" and strategy_id:
            await self._place_protective_stop(
                pool, broker, strategy_pool, strategy_id, symbol,
                order.filled_qty, order.filled_price,
            )

        # / post-analysis on close
        if side == "sell" and pnl is not None:
            entry_notional = float(entry_price) * float(order.filled_qty) if entry_price else 0.0
            trigger = _post_mortem_trigger(pnl, entry_notional)
            if trigger:
                _spawn_post_mortem(self.tasks, pool, log_id, strategy_id, symbol, pnl, trigger)

        return {
            "status": "filled",
            "log_id": log_id,
            "order_id": order.order_id,
            "qty": order.filled_qty,
            "price": order.filled_price,
        }

    async def _update_position_and_pnl(
        self, pool, side: str, strategy_id: str | None, symbol: str, order: Any,
    ) -> tuple[float | None, float | None]:
        if side == "buy" and strategy_id:
            await open_strategy_position(
                pool, strategy_id, symbol, order.filled_qty, order.filled_price,
            )
            return None, None
        if side != "sell":
            return None, None
        entry_price: float | None = None
        if strategy_id:
            entry_price = await close_strategy_position(
                pool, strategy_id, symbol, order.filled_qty,
            )
        if entry_price is None:
            fallback = await fetch_most_recent_open_entry(pool, symbol)
            if fallback and fallback.get("entry_price") is not None:
                entry_price = fallback["entry_price"]
            else:
                logger.warning("sell_without_entry_history", symbol=symbol, qty=order.filled_qty)
        pnl = None
        if entry_price is not None:
            pnl = (order.filled_price - entry_price) * order.filled_qty
        return pnl, entry_price

    async def _place_protective_stop(
        self, pool, broker: BrokerInterface, strategy_pool,
        strategy_id: str, symbol: str, qty: float, fill_price: float,
    ) -> None:
        # / never fails the buy
        if strategy_pool is None or is_crypto(symbol):
            return
        stop_distance = self._infer_stop_distance(strategy_pool, strategy_id)
        if not (stop_distance and stop_distance > 0):
            return
        if fill_price is None or fill_price <= 0 or qty is None or qty <= 0:
            return
        stop_price = round(float(fill_price) * (1 - stop_distance), 2)
        try:
            stop_order = await broker.place_order(
                symbol=symbol, qty=qty, side="sell",
                order_type="stop", stop_price=stop_price,
            )
        except Exception as exc:
            logger.error(
                "protective_stop_failed",
                strategy_id=strategy_id, symbol=symbol,
                stop_price=stop_price, error=str(exc)[:160],
            )
            await log_event(
                pool, level="error", source="executor",
                message="protective_stop_failed", symbol=symbol,
                details={
                    "strategy_id": strategy_id, "stop_price": stop_price,
                    "qty": float(qty), "error": str(exc)[:200],
                },
            )
            return
        stop_id = getattr(stop_order, "order_id", None)
        if not stop_id:
            logger.warning(
                "protective_stop_no_id",
                strategy_id=strategy_id, symbol=symbol, stop_price=stop_price,
            )
            return
        self._protective_stops[(strategy_id, symbol)] = stop_id
        logger.info(
            "protective_stop_placed",
            strategy_id=strategy_id, symbol=symbol,
            stop_price=stop_price, qty=qty, stop_order_id=stop_id,
        )
        await log_event(
            pool, level="info", source="executor",
            message="protective_stop_placed", symbol=symbol,
            details={
                "strategy_id": strategy_id, "stop_price": stop_price,
                "qty": float(qty), "stop_order_id": stop_id,
            },
        )

    async def _cancel_protective_stop(
        self, broker: BrokerInterface, strategy_id: str | None, symbol: str,
    ) -> None:
        # / ignore cancel failures
        if not strategy_id:
            return
        stop_id = self._protective_stops.pop((strategy_id, symbol), None)
        if not stop_id:
            return
        try:
            await broker.cancel_order(stop_id)
            logger.info(
                "protective_stop_cancelled",
                strategy_id=strategy_id, symbol=symbol, stop_order_id=stop_id,
            )
        except Exception as exc:
            logger.warning(
                "protective_stop_cancel_failed",
                strategy_id=strategy_id, symbol=symbol,
                stop_order_id=stop_id, error=str(exc)[:160],
            )

    def _infer_stop_distance(self, strategy_pool, strategy_id: str | None) -> float | None:
        # / mirrors risk agent
        if not strategy_id:
            return None
        try:
            entry = strategy_pool.get(strategy_id)
            if not entry:
                return None
            cfg = entry.strategy.config
            sl = cfg.get("exit_conditions", {}).get("stop_loss", {}) or {}
            t = (sl.get("type") or "fixed_pct").lower()
            if t in ("fixed_pct", "percent", "pct"):
                pct = sl.get("pct")
                return float(pct) if pct else None
            if "atr" in t or t in ("trailing", "chandelier"):
                return 0.02
        except Exception as exc:
            logger.debug("stop_distance_infer_failed", strategy_id=strategy_id, error=str(exc)[:100])
        return None

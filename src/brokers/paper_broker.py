# / simulated broker for backtesting and paper trading
# / tracks positions, fills orders instantly at current price
# / no real api calls — everything in memory

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import structlog

from .base import AccountBalance, BrokerInterface, Order, Position

logger = structlog.get_logger(__name__)


class PaperBroker(BrokerInterface):
    def __init__(self, initial_cash: float = 100_000.0):
        self.cash = initial_cash
        self.initial_cash = initial_cash
        self.positions: dict[str, dict[str, float]] = {}  # symbol -> {qty, avg_price}
        self.orders: dict[str, Order] = {}
        self.prices: dict[str, float] = {}  # latest prices
        self._trade_log: list[dict[str, Any]] = []

    def set_price(self, symbol: str, price: float) -> None:
        # / manually set price for a symbol (used in backtesting)
        self.prices[symbol] = price

    def set_prices(self, prices: dict[str, float]) -> None:
        # / bulk set prices
        self.prices.update(prices)

    async def get_price(self, symbol: str) -> float:
        price = self.prices.get(symbol)
        if price is None:
            raise ValueError(f"no price available for {symbol}")
        return price

    async def place_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> Order:
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be buy or sell, got {side}")

        order_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        price = self.prices.get(symbol)
        if price is None:
            # / reject if no price available
            order = Order(
                order_id=order_id, symbol=symbol, side=side, qty=qty,
                order_type=order_type, status="rejected",
                created_at=now, details={"reason": "no_price_available"},
            )
            self.orders[order_id] = order
            return order

        # / for limit orders, check if fillable
        if order_type == "limit" and limit_price is not None:
            if side == "buy" and price > limit_price:
                order = Order(
                    order_id=order_id, symbol=symbol, side=side, qty=qty,
                    order_type=order_type, status="pending",
                    limit_price=limit_price, created_at=now,
                )
                self.orders[order_id] = order
                return order
            if side == "sell" and price < limit_price:
                order = Order(
                    order_id=order_id, symbol=symbol, side=side, qty=qty,
                    order_type=order_type, status="pending",
                    limit_price=limit_price, created_at=now,
                )
                self.orders[order_id] = order
                return order

        # / reject unsupported order types
        if order_type not in ("market", "limit"):
            raise ValueError(f"unsupported order type: {order_type} (stop/stop_limit not yet implemented)")

        # / market orders and fillable limit orders: fill immediately
        # / limit orders fill at the better price (market vs limit)
        if order_type == "limit" and limit_price:
            fill_price = min(limit_price, price) if side == "buy" else max(limit_price, price)
        else:
            fill_price = price
        cost = fill_price * qty

        if side == "buy":
            if cost > self.cash:
                order = Order(
                    order_id=order_id, symbol=symbol, side=side, qty=qty,
                    order_type=order_type, status="rejected",
                    created_at=now, details={"reason": "insufficient_cash"},
                )
                self.orders[order_id] = order
                return order

            self.cash -= cost
            pos = self.positions.get(symbol, {"qty": 0.0, "avg_price": 0.0})
            total_qty = pos["qty"] + qty
            if total_qty > 0:
                pos["avg_price"] = (pos["avg_price"] * pos["qty"] + fill_price * qty) / total_qty
            pos["qty"] = total_qty
            self.positions[symbol] = pos

        elif side == "sell":
            pos = self.positions.get(symbol, {"qty": 0.0, "avg_price": 0.0})
            if pos["qty"] < qty:
                order = Order(
                    order_id=order_id, symbol=symbol, side=side, qty=qty,
                    order_type=order_type, status="rejected",
                    created_at=now, details={"reason": "insufficient_position"},
                )
                self.orders[order_id] = order
                return order

            self.cash += cost
            pos["qty"] -= qty
            if abs(pos["qty"]) < 1e-9:
                del self.positions[symbol]
            else:
                self.positions[symbol] = pos

        order = Order(
            order_id=order_id, symbol=symbol, side=side, qty=qty,
            order_type=order_type, status="filled",
            filled_qty=qty, filled_price=fill_price,
            limit_price=limit_price, stop_price=stop_price,
            created_at=now, filled_at=now,
        )
        self.orders[order_id] = order

        self._trade_log.append({
            "order_id": order_id, "symbol": symbol, "side": side,
            "qty": qty, "price": fill_price, "time": now,
        })

        logger.info(
            "paper_order_filled",
            order_id=order_id, symbol=symbol, side=side,
            qty=qty, price=fill_price,
        )
        return order

    async def get_positions(self) -> list[Position]:
        result = []
        for symbol, pos in self.positions.items():
            price = self.prices.get(symbol, pos["avg_price"])
            market_value = pos["qty"] * price
            unrealized = (price - pos["avg_price"]) * pos["qty"]
            result.append(Position(
                symbol=symbol,
                qty=pos["qty"],
                avg_entry_price=pos["avg_price"],
                current_price=price,
                market_value=market_value,
                unrealized_pnl=unrealized,
            ))
        return result

    async def get_account_balance(self) -> AccountBalance:
        positions_value = sum(
            pos["qty"] * self.prices.get(sym, pos["avg_price"])
            for sym, pos in self.positions.items()
        )
        equity = self.cash + positions_value
        return AccountBalance(
            equity=equity,
            cash=self.cash,
            buying_power=self.cash,
            portfolio_value=equity,
            positions_value=positions_value,
        )

    async def cancel_order(self, order_id: str) -> bool:
        order = self.orders.get(order_id)
        if not order:
            return False
        if order.status == "pending":
            order.status = "cancelled"
            return True
        return False

    async def get_order_status(self, order_id: str) -> Order:
        order = self.orders.get(order_id)
        if not order:
            raise ValueError(f"order not found: {order_id}")
        return order

    async def stream_prices(
        self,
        symbols: list[str],
        callback: Callable[[str, float], Any],
    ) -> None:
        # / paper broker doesn't stream — just calls back current prices
        for symbol in symbols:
            price = self.prices.get(symbol)
            if price is not None:
                await callback(symbol, price)

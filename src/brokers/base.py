# / abstract broker interface — all brokers implement this
# / strategies never touch brokers directly, always through this interface

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable


@dataclass
class Order:
    order_id: str
    symbol: str
    side: str           # buy, sell
    qty: float
    order_type: str     # market, limit, stop, stop_limit
    status: str         # pending, filled, partial, cancelled, rejected
    filled_qty: float = 0.0
    filled_price: float | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    created_at: datetime | None = None
    filled_at: datetime | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    side: str = "long"  # long, short


@dataclass
class AccountBalance:
    equity: float
    cash: float
    buying_power: float
    portfolio_value: float
    positions_value: float


class BrokerInterface(ABC):
    @abstractmethod
    async def get_price(self, symbol: str) -> float:
        # / get current/latest price for a symbol
        ...

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> Order:
        # / place an order, returns order object
        ...

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        # / get all open positions
        ...

    @abstractmethod
    async def get_account_balance(self) -> AccountBalance:
        # / get account equity, cash, buying power
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        # / cancel an open order, returns success
        ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> Order:
        # / get current status of an order
        ...

    @abstractmethod
    async def stream_prices(
        self,
        symbols: list[str],
        callback: Callable[[str, float], Any],
    ) -> None:
        # / stream real-time prices, calls callback(symbol, price)
        ...

# / tests for the abstract broker interface contract

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import pytest

from src.brokers.base import (
    AccountBalance,
    BrokerInterface,
    Order,
    Position,
)


class TestOrderDataclass:
    def test_required_fields_present(self):
        o = Order(
            order_id="abc", symbol="AAPL", side="buy",
            qty=10.0, order_type="market", status="pending",
        )
        assert o.order_id == "abc"
        assert o.symbol == "AAPL"
        assert o.side == "buy"
        assert o.qty == 10.0
        assert o.status == "pending"

    def test_defaults(self):
        o = Order(
            order_id="abc", symbol="AAPL", side="buy",
            qty=10.0, order_type="market", status="pending",
        )
        assert o.filled_qty == 0.0
        assert o.filled_price is None
        assert o.limit_price is None
        assert o.stop_price is None
        assert o.created_at is None
        assert o.filled_at is None
        assert o.details == {}

    def test_details_is_isolated_per_instance(self):
        # / regression: field(default_factory=dict) must give each Order its own dict
        a = Order(order_id="1", symbol="X", side="buy", qty=1, order_type="market", status="pending")
        b = Order(order_id="2", symbol="Y", side="buy", qty=1, order_type="market", status="pending")
        a.details["flag"] = True
        assert "flag" not in b.details


class TestPositionDataclass:
    def test_fields_and_default_side(self):
        p = Position(
            symbol="AAPL", qty=5.0, avg_entry_price=150.0,
            current_price=155.0, market_value=775.0, unrealized_pnl=25.0,
        )
        assert p.symbol == "AAPL"
        assert p.side == "long"

    def test_short_side(self):
        p = Position(
            symbol="AAPL", qty=-5.0, avg_entry_price=150.0,
            current_price=145.0, market_value=-725.0, unrealized_pnl=25.0,
            side="short",
        )
        assert p.side == "short"


class TestAccountBalance:
    def test_fields(self):
        ab = AccountBalance(
            equity=100_000, cash=50_000, buying_power=150_000,
            portfolio_value=100_000, positions_value=50_000,
        )
        assert ab.equity == 100_000
        assert ab.buying_power == 150_000


class TestBrokerInterfaceABC:
    def test_is_abstract_and_cannot_instantiate(self):
        # / ABC with unimplemented abstracts must refuse direct instantiation
        with pytest.raises(TypeError):
            BrokerInterface()

    def test_partial_subclass_still_abstract(self):
        class PartialBroker(BrokerInterface):
            async def get_price(self, symbol: str) -> float:
                return 0.0
            # / other abstracts deliberately missing

        with pytest.raises(TypeError):
            PartialBroker()

    def test_abstract_method_names(self):
        expected = {
            "get_price", "place_order", "get_positions",
            "get_account_balance", "cancel_order",
            "get_order_status", "stream_prices",
        }
        assert BrokerInterface.__abstractmethods__ == expected

    def test_abstract_methods_are_coroutines(self):
        # / every abstract method declared on BrokerInterface must be async
        for name in BrokerInterface.__abstractmethods__:
            method = getattr(BrokerInterface, name)
            assert inspect.iscoroutinefunction(method), f"{name} must be async"


class _MinimalBroker(BrokerInterface):
    # / concrete subclass implementing every abstract method — used to verify contract
    async def get_price(self, symbol: str) -> float:
        return 123.45

    async def place_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        limit_price: float | None = None,
        stop_price: float | None = None,
        extended_hours: bool = False,
    ) -> Order:
        return Order(
            order_id="1", symbol=symbol, side=side, qty=qty,
            order_type=order_type, status="filled", filled_qty=qty,
            filled_price=123.45, limit_price=limit_price, stop_price=stop_price,
        )

    async def get_positions(self) -> list[Position]:
        return []

    async def get_account_balance(self) -> AccountBalance:
        return AccountBalance(
            equity=1.0, cash=1.0, buying_power=1.0,
            portfolio_value=1.0, positions_value=0.0,
        )

    async def cancel_order(self, order_id: str) -> bool:
        return True

    async def get_order_status(self, order_id: str) -> Order:
        return Order(
            order_id=order_id, symbol="X", side="buy", qty=1,
            order_type="market", status="filled",
        )

    async def stream_prices(
        self,
        symbols: list[str],
        callback: Callable[[str, float], Any],
    ) -> None:
        for s in symbols:
            callback(s, 100.0)


class TestConcreteImplementation:
    def test_can_instantiate_full_subclass(self):
        # / all abstracts implemented -> instantiation works
        broker = _MinimalBroker()
        assert isinstance(broker, BrokerInterface)

    def test_subclass_registered_as_interface(self):
        # / issubclass and isinstance both affirm the contract
        assert issubclass(_MinimalBroker, BrokerInterface)
        assert isinstance(_MinimalBroker(), BrokerInterface)

    @pytest.mark.asyncio
    async def test_minimal_broker_get_price(self):
        b = _MinimalBroker()
        assert await b.get_price("AAPL") == 123.45

    @pytest.mark.asyncio
    async def test_minimal_broker_place_order_returns_order(self):
        b = _MinimalBroker()
        order = await b.place_order("AAPL", 10, "buy")
        assert isinstance(order, Order)
        assert order.symbol == "AAPL"
        assert order.status == "filled"

    @pytest.mark.asyncio
    async def test_minimal_broker_stream_prices_invokes_callback(self):
        b = _MinimalBroker()
        received = []

        def cb(sym: str, price: float) -> None:
            received.append((sym, price))

        await b.stream_prices(["AAPL", "MSFT"], cb)
        assert received == [("AAPL", 100.0), ("MSFT", 100.0)]

    @pytest.mark.asyncio
    async def test_minimal_broker_balance_is_account_balance(self):
        b = _MinimalBroker()
        ab = await b.get_account_balance()
        assert isinstance(ab, AccountBalance)

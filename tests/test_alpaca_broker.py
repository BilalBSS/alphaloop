# / tests for alpaca broker (mocked — no real api calls)

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.brokers.alpaca_broker import AlpacaBroker, _parse_order

# / patch target for shared alpaca client
_ALPACA_CLIENT_PATCH = "src.brokers.alpaca_broker.get_alpaca_client"


def _mock_response(data: dict, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    resp.json.return_value = data
    return resp


class TestParseOrder:
    def test_filled_order(self):
        data = {
            "id": "abc123",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "type": "market",
            "status": "filled",
            "filled_qty": "10",
            "filled_avg_price": "150.50",
            "limit_price": None,
            "stop_price": None,
            "created_at": "2026-03-25T10:00:00Z",
            "filled_at": "2026-03-25T10:00:01Z",
        }
        order = _parse_order(data)
        assert order.order_id == "abc123"
        assert order.symbol == "AAPL"
        assert order.side == "buy"
        assert order.qty == 10.0
        assert order.status == "filled"
        assert order.filled_price == 150.50

    def test_pending_order(self):
        data = {
            "id": "xyz789",
            "symbol": "MSFT",
            "side": "sell",
            "qty": "5",
            "type": "limit",
            "status": "pending_new",
            "filled_qty": "0",
            "filled_avg_price": None,
            "limit_price": "310.00",
            "stop_price": None,
            "created_at": "2026-03-25T10:00:00Z",
            "filled_at": None,
        }
        order = _parse_order(data)
        assert order.status == "pending_new"
        assert order.limit_price == 310.0
        assert order.filled_price is None


class TestAlpacaBrokerGetPrice:
    @pytest.mark.asyncio
    async def test_stock_price(self):
        broker = AlpacaBroker()
        mock_resp = _mock_response({"trade": {"p": 155.25}})

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch(_ALPACA_CLIENT_PATCH, return_value=mock_client):
            with patch.dict("os.environ", {"ALPACA_API_KEY": "test", "ALPACA_SECRET_KEY": "test"}):
                price = await broker.get_price("AAPL")
        assert price == 155.25

    @pytest.mark.asyncio
    async def test_crypto_price(self):
        broker = AlpacaBroker()
        mock_resp = _mock_response({"trades": {"BTC/USD": {"p": 65000.0}}})

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch(_ALPACA_CLIENT_PATCH, return_value=mock_client):
            with patch.dict("os.environ", {"ALPACA_API_KEY": "test", "ALPACA_SECRET_KEY": "test"}):
                price = await broker.get_price("BTC-USD")
        assert price == 65000.0


class TestAlpacaBrokerPlaceOrder:
    @pytest.mark.asyncio
    async def test_market_order(self):
        broker = AlpacaBroker()
        order_data = {
            "id": "order123",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "type": "market",
            "status": "accepted",
            "filled_qty": "0",
            "filled_avg_price": None,
            "limit_price": None,
            "stop_price": None,
            "created_at": "2026-03-25T10:00:00Z",
            "filled_at": None,
        }
        mock_resp = _mock_response(order_data)

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp

        with patch(_ALPACA_CLIENT_PATCH, return_value=mock_client):
            with patch.dict("os.environ", {"ALPACA_API_KEY": "test", "ALPACA_SECRET_KEY": "test"}):
                order = await broker.place_order("AAPL", 10, "buy")
        assert order.order_id == "order123"
        assert order.side == "buy"


class TestAlpacaBrokerGetPositions:
    @pytest.mark.asyncio
    async def test_returns_positions(self):
        broker = AlpacaBroker()
        positions_data = [
            {
                "symbol": "AAPL",
                "qty": "10",
                "avg_entry_price": "150.00",
                "current_price": "155.00",
                "market_value": "1550.00",
                "unrealized_pl": "50.00",
            }
        ]
        mock_resp = _mock_response(positions_data)

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch(_ALPACA_CLIENT_PATCH, return_value=mock_client):
            with patch.dict("os.environ", {"ALPACA_API_KEY": "test", "ALPACA_SECRET_KEY": "test"}):
                positions = await broker.get_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "AAPL"
        assert positions[0].unrealized_pnl == 50.0


class TestAlpacaBrokerGetAccount:
    @pytest.mark.asyncio
    async def test_returns_balance(self):
        broker = AlpacaBroker()
        account_data = {
            "equity": "100000.00",
            "cash": "90000.00",
            "buying_power": "90000.00",
            "portfolio_value": "100000.00",
        }
        mock_resp = _mock_response(account_data)

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch(_ALPACA_CLIENT_PATCH, return_value=mock_client):
            with patch.dict("os.environ", {"ALPACA_API_KEY": "test", "ALPACA_SECRET_KEY": "test"}):
                balance = await broker.get_account_balance()
        assert balance.equity == 100000.0
        assert balance.cash == 90000.0


class TestAlpacaBrokerCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_success(self):
        broker = AlpacaBroker()
        mock_resp = MagicMock()
        mock_resp.status_code = 204

        mock_client = AsyncMock()
        mock_client.delete.return_value = mock_resp

        with patch(_ALPACA_CLIENT_PATCH, return_value=mock_client):
            with patch.dict("os.environ", {"ALPACA_API_KEY": "test", "ALPACA_SECRET_KEY": "test"}):
                result = await broker.cancel_order("order123")
        assert result is True


# ---------- new deep tests ----------


class TestParseOrderDeep:
    def test_cancelled_status(self):
        data = {
            "id": "cancel123",
            "symbol": "TSLA",
            "side": "buy",
            "qty": "20",
            "type": "limit",
            "status": "canceled",
            "filled_qty": "0",
            "filled_avg_price": None,
            "limit_price": "200.00",
            "stop_price": None,
            "created_at": "2026-03-25T10:00:00Z",
            "filled_at": None,
        }
        order = _parse_order(data)
        assert order.status == "canceled"
        assert order.filled_qty == 0.0
        assert order.filled_price is None
        assert order.limit_price == 200.0

    def test_partial_fill(self):
        data = {
            "id": "partial123",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "100",
            "type": "limit",
            "status": "partially_filled",
            "filled_qty": "37",
            "filled_avg_price": "149.50",
            "limit_price": "150.00",
            "stop_price": None,
            "created_at": "2026-03-25T10:00:00Z",
            "filled_at": None,
        }
        order = _parse_order(data)
        assert order.status == "partially_filled"
        assert order.qty == 100.0
        assert order.filled_qty == 37.0
        assert order.filled_price == 149.50

    def test_missing_optional_fields(self):
        # / minimal data with missing optional fields
        data = {
            "id": "min123",
            "symbol": "GOOG",
            "side": "sell",
            "qty": "5",
            "type": "market",
            "status": "filled",
            "filled_avg_price": "2800.00",
            "created_at": None,
            "filled_at": None,
        }
        order = _parse_order(data)
        assert order.order_id == "min123"
        assert order.qty == 5.0
        assert order.filled_qty == 0.0  # / missing filled_qty defaults to 0
        assert order.limit_price is None
        assert order.stop_price is None
        assert order.created_at is None
        assert order.filled_at is None


class TestAlpacaBrokerGetPriceDeep:
    @pytest.mark.asyncio
    async def test_raises_on_zero_price(self):
        # / price <= 0 should raise ValueError
        # / reset circuit breaker before test
        from src.data.resilience import _breakers
        _breakers.pop("alpaca_broker", None)

        broker = AlpacaBroker()
        mock_resp = _mock_response({"trade": {"p": 0}})

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch(_ALPACA_CLIENT_PATCH, return_value=mock_client):
            with patch.dict("os.environ", {"ALPACA_API_KEY": "test", "ALPACA_SECRET_KEY": "test"}):
                with pytest.raises(Exception):
                    await broker.get_price("AAPL")

    @pytest.mark.asyncio
    async def test_raises_on_negative_price(self):
        # / reset circuit breaker before test
        from src.data.resilience import _breakers
        _breakers.pop("alpaca_broker", None)

        broker = AlpacaBroker()
        mock_resp = _mock_response({"trade": {"p": -5.0}})

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch(_ALPACA_CLIENT_PATCH, return_value=mock_client):
            with patch.dict("os.environ", {"ALPACA_API_KEY": "test", "ALPACA_SECRET_KEY": "test"}):
                with pytest.raises(Exception):
                    await broker.get_price("AAPL")


class TestAlpacaBrokerCancelOrderDeep:
    @pytest.mark.asyncio
    async def test_cancel_returns_false_on_404(self):
        # / reset circuit breaker before test
        from src.data.resilience import _breakers
        _breakers.pop("alpaca_broker", None)

        broker = AlpacaBroker()
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        mock_client = AsyncMock()
        mock_client.delete.return_value = mock_resp

        with patch(_ALPACA_CLIENT_PATCH, return_value=mock_client):
            with patch.dict("os.environ", {"ALPACA_API_KEY": "test", "ALPACA_SECRET_KEY": "test"}):
                result = await broker.cancel_order("nonexistent")
        assert result is False

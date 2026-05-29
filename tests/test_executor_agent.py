# / tests for executor agent

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.executor_agent import ExecutorAgent
from src.brokers.base import Order

pytestmark = pytest.mark.usefixtures("default_executor_unpaused")

# ---------------------------------------------------------------------------
# / helpers
# ---------------------------------------------------------------------------

def _mock_pool(mock_conn=None):
    if mock_conn is None:
        mock_conn = AsyncMock()
    # / atomic status update returns "UPDATE 1" by default
    mock_conn.execute.return_value = "UPDATE 1"
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


def _make_trade_row(
    status: str = "pending", symbol: str = "AAPL",
    side: str = "buy", qty: float = 10.0,
    order_type: str = "market", strategy_id: str = "strat_001",
) -> dict:
    return {
        "id": 1, "signal_id": 5, "symbol": symbol,
        "side": side, "qty": qty, "order_type": order_type,
        "status": status, "strategy_id": strategy_id,
    }


def _make_filled_order(
    order_id: str = "ord_123", filled_qty: float = 10.0,
    filled_price: float = 150.0,
) -> Order:
    return Order(
        order_id=order_id, symbol="AAPL", side="buy", qty=10.0,
        order_type="market", status="filled",
        filled_qty=filled_qty, filled_price=filled_price,
    )


def _make_rejected_order() -> Order:
    return Order(
        order_id="ord_456", symbol="AAPL", side="buy", qty=10.0,
        order_type="market", status="rejected",
        details={"reason": "insufficient funds"},
    )


def _make_pending_order() -> Order:
    return Order(
        order_id="ord_789", symbol="AAPL", side="buy", qty=10.0,
        order_type="market", status="pending",
    )


def _make_broker(order: Order | None = None) -> AsyncMock:
    broker = AsyncMock()
    broker.place_order.return_value = order or _make_filled_order()
    return broker


# ---------------------------------------------------------------------------
# / filled order tests
# ---------------------------------------------------------------------------

class TestExecutorFilled:
    def setup_method(self):
        self.agent = ExecutorAgent()

    @pytest.mark.asyncio
    async def test_execute_filled(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            _make_trade_row(),  # / approved_trades fetch
            {"regime": "bull"},  # / regime_history fetch
        ]
        pool = _mock_pool(mock_conn)
        broker = _make_broker(_make_filled_order())

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock) as mock_update,
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=100) as mock_log,
        ):
            result = await self.agent.execute_trade(pool, 1, broker)

        assert result["status"] == "filled"
        assert result["log_id"] == 100
        assert result["order_id"] == "ord_123"
        assert result["qty"] == 10.0
        assert result["price"] == 150.0
        mock_log.assert_called_once()

    @pytest.mark.asyncio
    async def test_filled_order_details(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            _make_trade_row(),
            None,  # / no regime
        ]
        pool = _mock_pool(mock_conn)
        broker = _make_broker(_make_filled_order(filled_qty=5.0, filled_price=155.0))

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=101) as mock_log,
        ):
            result = await self.agent.execute_trade(pool, 1, broker)

        assert result["qty"] == 5.0
        assert result["price"] == 155.0
        # / verify store_trade_log called with correct fields
        call_kwargs = mock_log.call_args.kwargs
        assert call_kwargs["symbol"] == "AAPL"
        assert call_kwargs["side"] == "buy"
        assert call_kwargs["qty"] == 5.0
        assert call_kwargs["price"] == 155.0

    @pytest.mark.asyncio
    async def test_pnl_is_none_for_entry(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            _make_trade_row(),
            None,
        ]
        pool = _mock_pool(mock_conn)
        broker = _make_broker()

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=100) as mock_log,
        ):
            await self.agent.execute_trade(pool, 1, broker)

        call_kwargs = mock_log.call_args.kwargs
        assert call_kwargs["pnl"] is None

    @pytest.mark.asyncio
    async def test_strategy_id_passed_through(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            _make_trade_row(strategy_id="my_strat_42"),
            None,
        ]
        pool = _mock_pool(mock_conn)
        broker = _make_broker()

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=100) as mock_log,
        ):
            await self.agent.execute_trade(pool, 1, broker)

        call_kwargs = mock_log.call_args.kwargs
        assert call_kwargs["strategy_id"] == "my_strat_42"

    @pytest.mark.asyncio
    async def test_regime_fetched_for_log(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            _make_trade_row(),
            {"regime": "high_vol"},  # / regime_history
        ]
        pool = _mock_pool(mock_conn)
        broker = _make_broker()

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=100) as mock_log,
        ):
            await self.agent.execute_trade(pool, 1, broker)

        call_kwargs = mock_log.call_args.kwargs
        assert call_kwargs["regime"] == "high_vol"

    @pytest.mark.asyncio
    async def test_status_transitions(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            _make_trade_row(status="pending"),
            None,
        ]
        pool = _mock_pool(mock_conn)
        broker = _make_broker()

        statuses = []

        async def _track_status(pool, table, row_id, status):
            statuses.append(status)

        with (
            patch("src.agents.executor_agent.update_trade_status", side_effect=_track_status),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=100),
        ):
            await self.agent.execute_trade(pool, 1, broker)

        # / executing is set atomically via conn.execute, filled via tools.update_trade_status
        assert statuses == ["filled"]


# ---------------------------------------------------------------------------
# / rejection / error tests
# ---------------------------------------------------------------------------

class TestExecutorRejection:
    def setup_method(self):
        self.agent = ExecutorAgent()

    @pytest.mark.asyncio
    async def test_execute_rejected(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_trade_row()
        pool = _mock_pool(mock_conn)
        broker = _make_broker(_make_rejected_order())

        with patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock) as mock_update:
            result = await self.agent.execute_trade(pool, 1, broker)

        assert result["status"] == "failed"
        assert result["reason"] == "rejected"
        # / check status set to failed
        update_calls = [c for c in mock_update.call_args_list if "failed" in str(c)]
        assert len(update_calls) >= 1

    @pytest.mark.asyncio
    async def test_double_execution_guard(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_trade_row(status="filled")
        pool = _mock_pool(mock_conn)
        mock_conn.execute.return_value = "UPDATE 0"  # / atomic guard rejects non-pending (after pool setup)
        broker = _make_broker()

        result = await self.agent.execute_trade(pool, 1, broker)
        assert result["status"] == "skipped"
        assert "status_is_filled" in result["reason"]
        # / broker should NOT have been called
        broker.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_trade_not_found(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        pool = _mock_pool(mock_conn)
        broker = _make_broker()

        result = await self.agent.execute_trade(pool, 999, broker)
        assert result["status"] == "error"
        assert result["reason"] == "trade_not_found"

    @pytest.mark.asyncio
    async def test_broker_exception(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_trade_row()
        pool = _mock_pool(mock_conn)
        broker = _make_broker()
        broker.place_order.side_effect = Exception("connection timeout")

        with patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock) as mock_update:
            result = await self.agent.execute_trade(pool, 1, broker)

        assert result["status"] == "error"
        assert "connection timeout" in result["reason"]
        # / status should be set to error
        error_calls = [c for c in mock_update.call_args_list if "error" in str(c)]
        assert len(error_calls) >= 1


class TestExecutorFillMissingPrice:
    def setup_method(self):
        self.agent = ExecutorAgent()

    @pytest.mark.asyncio
    async def test_filled_order_with_none_price_returns_failed(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_trade_row()
        pool = _mock_pool(mock_conn)
        bad_order = _make_filled_order(filled_qty=10.0, filled_price=None)
        broker = _make_broker(bad_order)

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock) as mock_update,
            patch("src.agents.executor_agent.log_event", new_callable=AsyncMock) as mock_log,
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock) as mock_store,
        ):
            result = await self.agent.execute_trade(pool, 1, broker)

        assert result["status"] == "failed"
        assert result["reason"] == "fill_missing_price"
        mock_store.assert_not_called()
        reconcile_calls = [c for c in mock_update.call_args_list if "pending_reconcile" in str(c)]
        assert len(reconcile_calls) >= 1
        assert mock_log.called
        log_args = mock_log.call_args
        assert log_args.kwargs.get("level") == "error"
        assert log_args.kwargs.get("message") == "fill_missing_price"

    @pytest.mark.asyncio
    async def test_filled_order_with_zero_price_returns_failed(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_trade_row()
        pool = _mock_pool(mock_conn)
        broker = _make_broker(_make_filled_order(filled_qty=10.0, filled_price=0.0))

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.log_event", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock) as mock_store,
        ):
            result = await self.agent.execute_trade(pool, 1, broker)

        assert result["status"] == "failed"
        assert result["reason"] == "fill_missing_price"
        mock_store.assert_not_called()

    @pytest.mark.asyncio
    async def test_filled_order_with_real_price_proceeds(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [_make_trade_row(), {"regime": "bull"}]
        pool = _mock_pool(mock_conn)
        broker = _make_broker(_make_filled_order(filled_qty=10.0, filled_price=150.0))

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=42),
        ):
            result = await self.agent.execute_trade(pool, 1, broker)

        assert result["status"] == "filled"
        assert result["price"] == 150.0


# ---------------------------------------------------------------------------
# / other order statuses
# ---------------------------------------------------------------------------

class TestExecutorOtherStatuses:
    def setup_method(self):
        self.agent = ExecutorAgent()

    @pytest.mark.asyncio
    async def test_pending_order(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_trade_row()
        pool = _mock_pool(mock_conn)
        broker = _make_broker(_make_pending_order())

        with patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock):
            result = await self.agent.execute_trade(pool, 1, broker)

        assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_cancelled_order(self):
        cancelled_order = Order(
            order_id="ord_000", symbol="AAPL", side="buy", qty=10.0,
            order_type="market", status="cancelled",
            details={"reason": "user cancelled"},
        )
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_trade_row()
        pool = _mock_pool(mock_conn)
        broker = _make_broker(cancelled_order)

        with patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock):
            result = await self.agent.execute_trade(pool, 1, broker)

        assert result["status"] == "failed"
        assert result["reason"] == "cancelled"

    @pytest.mark.asyncio
    async def test_partial_order(self):
        partial_order = Order(
            order_id="ord_partial", symbol="AAPL", side="buy", qty=10.0,
            order_type="market", status="partial",
            filled_qty=5.0, filled_price=150.0,
        )
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_trade_row()
        pool = _mock_pool(mock_conn)
        broker = _make_broker(partial_order)

        with patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock):
            result = await self.agent.execute_trade(pool, 1, broker)

        assert result["status"] == "partial"

    @pytest.mark.asyncio
    async def test_broker_name_in_trade_log(self):
        # / type(broker).__name__ should appear in trade log
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            _make_trade_row(),
            None,  # / no regime
        ]
        pool = _mock_pool(mock_conn)
        broker = _make_broker()

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=100) as mock_log,
        ):
            await self.agent.execute_trade(pool, 1, broker)

        call_kwargs = mock_log.call_args.kwargs
        assert call_kwargs["broker"] == "AsyncMock"

    @pytest.mark.asyncio
    async def test_double_guard_executing_status(self):
        # / status='executing' should be skipped by atomic WHERE
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_trade_row(status="executing")
        pool = _mock_pool(mock_conn)
        mock_conn.execute.return_value = "UPDATE 0"  # / atomic guard rejects non-pending
        broker = _make_broker()

        result = await self.agent.execute_trade(pool, 1, broker)
        assert result["status"] == "skipped"
        assert "status_is_executing" in result["reason"]


# ---------------------------------------------------------------------------
# / killed strategy gate (bug 1b: orphan approved_trades must not execute)
# ---------------------------------------------------------------------------

class TestExecutorKilledStrategyGate:
    def setup_method(self):
        self.agent = ExecutorAgent()

    @pytest.mark.asyncio
    async def test_rejects_trade_when_strategy_killed(self):
        # / bug 1b: trade must be cancelled, not placed
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_trade_row(strategy_id="strat_doomed")
        pool = _mock_pool(mock_conn)
        broker = _make_broker()

        killed_entry = MagicMock()
        killed_entry.status = "killed"
        strategy_pool = MagicMock()
        strategy_pool.get.return_value = killed_entry

        with patch(
            "src.agents.executor_agent.update_trade_status", new_callable=AsyncMock
        ) as m_update:
            result = await self.agent.execute_trade(
                pool, 1, broker, strategy_pool=strategy_pool,
            )

        assert result["status"] == "cancelled"
        assert "strat_doomed_killed" in result["reason"]
        # / broker must NOT be called
        broker.place_order.assert_not_called()
        # / approved_trades.status must flip to killed_strategy (<=20 chars, fits VARCHAR)
        m_update.assert_called_once()
        new_status = m_update.call_args.args[3]
        assert new_status == "killed_strategy"
        assert len(new_status) <= 20

    @pytest.mark.asyncio
    async def test_allows_trade_when_strategy_live(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            _make_trade_row(),
            None,  # / no account balance row
            None,  # / no regime
        ]
        pool = _mock_pool(mock_conn)
        broker = _make_broker()

        live_entry = MagicMock()
        live_entry.status = "live"
        strategy_pool = MagicMock()
        strategy_pool.get.return_value = live_entry

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=100),
        ):
            result = await self.agent.execute_trade(
                pool, 1, broker, strategy_pool=strategy_pool,
            )

        # / live strategy -> broker called, no cancellation
        broker.place_order.assert_called_once()
        assert result["status"] != "cancelled"

    @pytest.mark.asyncio
    async def test_no_strategy_pool_skips_gate(self):
        # / backwards-compat: if caller doesn't pass strategy_pool, execute normally
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            _make_trade_row(),
            None,
            None,
        ]
        pool = _mock_pool(mock_conn)
        broker = _make_broker()

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=100),
        ):
            await self.agent.execute_trade(pool, 1, broker)

        broker.place_order.assert_called_once()


# ---------------------------------------------------------------------------
# / broker-enforced protective stops
# ---------------------------------------------------------------------------

def _stop_pool(trade_row: dict, regime=None) -> MagicMock:
    mock_conn = AsyncMock()
    mock_conn.fetchrow.side_effect = [trade_row, regime]
    return _mock_pool(mock_conn)


def _strategy_pool_with_stop(stop_loss: dict | None, strategy_id: str = "strat_001") -> MagicMock:
    entry = MagicMock()
    entry.status = "live"
    entry.strategy.config = {"exit_conditions": {"stop_loss": stop_loss}} if stop_loss is not None else {}
    sp = MagicMock()
    sp.get.return_value = entry
    return sp


def _stop_order(order_id: str = "stop_111") -> Order:
    return Order(
        order_id=order_id, symbol="AAPL", side="sell", qty=10.0,
        order_type="stop", status="pending", stop_price=142.5,
    )


class TestExecutorProtectiveStop:
    def setup_method(self):
        self.agent = ExecutorAgent()

    @pytest.mark.asyncio
    async def test_buy_fill_places_stop_at_right_price(self):
        pool = _stop_pool(_make_trade_row(side="buy"), regime={"regime": "bull"})
        broker = _make_broker(_make_filled_order(filled_price=150.0))
        broker.place_order.side_effect = [_make_filled_order(filled_price=150.0), _stop_order()]
        strategy_pool = _strategy_pool_with_stop({"type": "fixed_pct", "pct": 0.05})

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=100),
            patch("src.agents.executor_agent.log_event", new_callable=AsyncMock),
        ):
            result = await self.agent.execute_trade(pool, 1, broker, strategy_pool=strategy_pool)

        assert result["status"] == "filled"
        # / stop is second order
        assert broker.place_order.call_count == 2
        stop_kwargs = broker.place_order.call_args_list[1].kwargs
        assert stop_kwargs["order_type"] == "stop"
        assert stop_kwargs["side"] == "sell"
        assert stop_kwargs["stop_price"] == 142.5  # / 5pct below 150
        assert self.agent._protective_stops[("strat_001", "AAPL")] == "stop_111"

    @pytest.mark.asyncio
    async def test_atr_stop_uses_default_distance(self):
        pool = _stop_pool(_make_trade_row(side="buy"), regime=None)
        broker = _make_broker(_make_filled_order(filled_price=100.0))
        broker.place_order.side_effect = [_make_filled_order(filled_price=100.0), _stop_order()]
        strategy_pool = _strategy_pool_with_stop({"type": "atr_trailing"})

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=100),
            patch("src.agents.executor_agent.log_event", new_callable=AsyncMock),
        ):
            await self.agent.execute_trade(pool, 1, broker, strategy_pool=strategy_pool)

        stop_kwargs = broker.place_order.call_args_list[1].kwargs
        assert stop_kwargs["stop_price"] == 98.0  # / atr default 2pct

    @pytest.mark.asyncio
    async def test_no_stop_config_places_no_stop(self):
        pool = _stop_pool(_make_trade_row(side="buy"), regime=None)
        broker = _make_broker(_make_filled_order(filled_price=150.0))
        strategy_pool = _strategy_pool_with_stop(None)

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=100),
            patch("src.agents.executor_agent.log_event", new_callable=AsyncMock),
        ):
            result = await self.agent.execute_trade(pool, 1, broker, strategy_pool=strategy_pool)

        assert result["status"] == "filled"
        broker.place_order.assert_called_once()  # / entry only, no stop

    @pytest.mark.asyncio
    async def test_no_strategy_pool_places_no_stop(self):
        pool = _stop_pool(_make_trade_row(side="buy"), regime=None)
        broker = _make_broker(_make_filled_order(filled_price=150.0))

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=100),
        ):
            result = await self.agent.execute_trade(pool, 1, broker)

        assert result["status"] == "filled"
        broker.place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_crypto_buy_skips_stop(self):
        trade = _make_trade_row(side="buy", symbol="BTC-USD")
        pool = _stop_pool(trade, regime=None)
        crypto_fill = Order(
            order_id="ord_btc", symbol="BTC-USD", side="buy", qty=0.5,
            order_type="market", status="filled",
            filled_qty=0.5, filled_price=60000.0,
        )
        broker = _make_broker(crypto_fill)
        strategy_pool = _strategy_pool_with_stop({"type": "fixed_pct", "pct": 0.05})

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=100),
            patch("src.agents.executor_agent.log_event", new_callable=AsyncMock),
        ):
            result = await self.agent.execute_trade(pool, 1, broker, strategy_pool=strategy_pool)

        assert result["status"] == "filled"
        broker.place_order.assert_called_once()  # / no stop for crypto

    @pytest.mark.asyncio
    async def test_stop_placement_failure_does_not_break_fill(self):
        pool = _stop_pool(_make_trade_row(side="buy"), regime=None)
        broker = _make_broker(_make_filled_order(filled_price=150.0))
        broker.place_order.side_effect = [
            _make_filled_order(filled_price=150.0),
            Exception("stop rejected"),
        ]
        strategy_pool = _strategy_pool_with_stop({"type": "fixed_pct", "pct": 0.05})

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=100),
            patch("src.agents.executor_agent.log_event", new_callable=AsyncMock) as mock_log,
        ):
            result = await self.agent.execute_trade(pool, 1, broker, strategy_pool=strategy_pool)

        assert result["status"] == "filled"  # / fill survives
        assert ("strat_001", "AAPL") not in self.agent._protective_stops
        failure_logs = [c for c in mock_log.call_args_list if c.kwargs.get("message") == "protective_stop_failed"]
        assert len(failure_logs) == 1

    @pytest.mark.asyncio
    async def test_sell_close_cancels_stop(self):
        self.agent._protective_stops[("strat_001", "AAPL")] = "stop_111"
        sell_fill = Order(
            order_id="ord_sell", symbol="AAPL", side="sell", qty=10.0,
            order_type="market", status="filled",
            filled_qty=10.0, filled_price=160.0,
        )
        pool = _stop_pool(_make_trade_row(side="sell"), regime=None)
        broker = _make_broker(sell_fill)

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=100),
            patch.object(ExecutorAgent, "_update_position_and_pnl", new_callable=AsyncMock, return_value=(40.0, 150.0)),
        ):
            result = await self.agent.execute_trade(pool, 1, broker)

        assert result["status"] == "filled"
        broker.cancel_order.assert_awaited_once_with("stop_111")
        assert ("strat_001", "AAPL") not in self.agent._protective_stops

    @pytest.mark.asyncio
    async def test_sell_close_without_tracked_stop_no_cancel(self):
        sell_fill = Order(
            order_id="ord_sell", symbol="AAPL", side="sell", qty=10.0,
            order_type="market", status="filled",
            filled_qty=10.0, filled_price=160.0,
        )
        pool = _stop_pool(_make_trade_row(side="sell"), regime=None)
        broker = _make_broker(sell_fill)

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=100),
            patch.object(ExecutorAgent, "_update_position_and_pnl", new_callable=AsyncMock, return_value=(40.0, 150.0)),
        ):
            await self.agent.execute_trade(pool, 1, broker)

        broker.cancel_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_sell_close_cancel_failure_does_not_break(self):
        self.agent._protective_stops[("strat_001", "AAPL")] = "stop_111"
        sell_fill = Order(
            order_id="ord_sell", symbol="AAPL", side="sell", qty=10.0,
            order_type="market", status="filled",
            filled_qty=10.0, filled_price=160.0,
        )
        pool = _stop_pool(_make_trade_row(side="sell"), regime=None)
        broker = _make_broker(sell_fill)
        broker.cancel_order.side_effect = Exception("already filled")

        with (
            patch("src.agents.executor_agent.update_trade_status", new_callable=AsyncMock),
            patch("src.agents.executor_agent.store_trade_log", new_callable=AsyncMock, return_value=100),
            patch.object(ExecutorAgent, "_update_position_and_pnl", new_callable=AsyncMock, return_value=(40.0, 150.0)),
        ):
            result = await self.agent.execute_trade(pool, 1, broker)

        assert result["status"] == "filled"
        assert ("strat_001", "AAPL") not in self.agent._protective_stops

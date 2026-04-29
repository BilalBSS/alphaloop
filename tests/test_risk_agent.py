# / tests for risk agent

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.risk_agent import RiskAgent
from src.brokers.base import AccountBalance, Position

# ---------------------------------------------------------------------------
# / helpers
# ---------------------------------------------------------------------------

def _mock_pool(mock_conn=None):
    if mock_conn is None:
        mock_conn = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


def _make_signal_row(
    symbol: str = "AAPL", signal_type: str = "buy",
    strength: float = 0.7, strategy_id: str = "strat_001",
    details: dict | None = None,
) -> dict:
    return {
        "id": 1, "symbol": symbol, "signal_type": signal_type,
        "strength": strength, "strategy_id": strategy_id,
        "regime": "bull", "details": details, "status": "pending",
    }


def _make_balance(equity: float = 100000.0, cash: float | None = None) -> AccountBalance:
    c = cash if cash is not None else equity
    return AccountBalance(
        equity=equity, cash=c,
        buying_power=c,
        portfolio_value=equity,
        positions_value=equity - c,
    )


@pytest.fixture(autouse=True)
def _patch_risk_tools():
    # / patch risk agent tool calls that use pool.acquire internally
    with (
        patch("src.agents.risk_agent.fetch_recent_pnl", new_callable=AsyncMock, return_value=[]),
        patch("src.agents.risk_agent.fetch_avg_volume", new_callable=AsyncMock, return_value=None),
        patch("src.agents.risk_agent.count_today_approved_trades", new_callable=AsyncMock, return_value=0),
        patch("src.agents.risk_agent.count_today_approved_trades_for_strategy", new_callable=AsyncMock, return_value=0),
        patch("src.agents.risk_agent.count_pending_signals_for_strategy", new_callable=AsyncMock, return_value=0),
        patch("src.agents.risk_agent.get_strategy_positions", new_callable=AsyncMock, return_value=[]),
        patch("src.agents.risk_agent.fetch_latest_regime", new_callable=AsyncMock, return_value={"regime": "bull"}),
        patch("src.agents.risk_agent.fetch_symbol_beta", new_callable=AsyncMock, return_value=None),
        patch("src.agents.circuit_breaker.fetch_peak_equity", new_callable=AsyncMock, return_value=0.0),
        patch("src.agents.circuit_breaker.store_peak_equity", new_callable=AsyncMock),
    ):
        yield


def _make_position(
    symbol: str = "MSFT", market_value: float = 5000.0,
) -> Position:
    return Position(
        symbol=symbol, qty=50, avg_entry_price=100.0,
        current_price=100.0, market_value=market_value,
        unrealized_pnl=0.0, side="long",
    )


def _make_broker(
    balance: AccountBalance | None = None,
    positions: list[Position] | None = None,
    price: float = 150.0,
) -> AsyncMock:
    broker = AsyncMock()
    broker.get_account_balance.return_value = balance or _make_balance()
    broker.get_positions.return_value = positions or []
    broker.get_price.return_value = price
    return broker


# ---------------------------------------------------------------------------
# / approval tests
# ---------------------------------------------------------------------------

class TestRiskAgentApproval:
    def setup_method(self):
        self.agent = RiskAgent(max_position_pct=0.08, max_portfolio_risk=0.25)

    @pytest.mark.asyncio
    async def test_approve_normal_signal(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row()
        pool = _mock_pool(mock_conn)
        broker = _make_broker(price=150.0)

        with (
            patch("src.agents.risk_agent.store_approved_trade", new_callable=AsyncMock, return_value=10),
            patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock),
        ):
            result = await self.agent.process_signal(pool, 1, broker)

        assert result["status"] == "approved"
        assert result["trade_id"] == 10
        assert result["symbol"] == "AAPL"
        assert result["qty"] > 0
        assert result["side"] == "buy"

    @pytest.mark.asyncio
    async def test_reject_zero_equity(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row()
        pool = _mock_pool(mock_conn)
        broker = _make_broker(balance=_make_balance(equity=0.0))

        with patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock):
            result = await self.agent.process_signal(pool, 1, broker)

        assert result["status"] == "rejected"
        assert result["reason"] == "zero_equity"

    @pytest.mark.asyncio
    async def test_reject_no_price(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row()
        pool = _mock_pool(mock_conn)
        broker = _make_broker()
        broker.get_price.side_effect = Exception("no price data")

        with patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock):
            result = await self.agent.process_signal(pool, 1, broker)

        assert result["status"] == "rejected"
        assert result["reason"] == "no_price"

    @pytest.mark.asyncio
    async def test_reject_qty_zero(self):
        mock_conn = AsyncMock()
        # / price extremely high so qty rounds to 0
        mock_conn.fetchrow.return_value = _make_signal_row(strength=0.01)
        pool = _mock_pool(mock_conn)
        broker = _make_broker(price=1000000.0)

        with patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock):
            result = await self.agent.process_signal(pool, 1, broker)

        assert result["status"] == "rejected"
        assert result["reason"] == "qty_zero"

    @pytest.mark.asyncio
    async def test_signal_not_found(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        pool = _mock_pool(mock_conn)
        broker = _make_broker()

        result = await self.agent.process_signal(pool, 999, broker)
        assert result["status"] == "skipped"
        assert result["reason"] == "signal_not_found_or_not_pending"

    @pytest.mark.asyncio
    async def test_whole_shares_only(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row(strength=0.5)
        pool = _mock_pool(mock_conn)
        broker = _make_broker(price=33.33)  # / produces fractional

        with (
            patch("src.agents.risk_agent.store_approved_trade", new_callable=AsyncMock, return_value=10),
            patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock),
        ):
            result = await self.agent.process_signal(pool, 1, broker)

        assert result["status"] == "approved"
        assert isinstance(result["qty"], int)

    @pytest.mark.asyncio
    async def test_sell_signal_approved(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row(signal_type="sell", details={"qty": 10})
        pool = _mock_pool(mock_conn)
        broker = _make_broker(positions=[_make_position(symbol="AAPL")])

        with (
            patch("src.agents.risk_agent.store_approved_trade", new_callable=AsyncMock, return_value=10),
            patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock),
        ):
            result = await self.agent.process_signal(pool, 1, broker)

        assert result["status"] == "approved"
        assert result["side"] == "sell"


# ---------------------------------------------------------------------------
# / portfolio risk tests
# ---------------------------------------------------------------------------

class TestPortfolioRisk:
    def setup_method(self):
        self.agent = RiskAgent(max_position_pct=0.08, max_portfolio_risk=0.25)

    @pytest.mark.asyncio
    async def test_portfolio_risk_size_down(self):
        # / existing positions use 20% of equity, new trade would push to 26%
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row(strength=1.0)
        pool = _mock_pool(mock_conn)

        positions = [_make_position("MSFT", market_value=20000.0)]
        broker = _make_broker(
            balance=_make_balance(equity=100000.0),
            positions=positions,
            price=100.0,
        )

        with (
            patch("src.agents.risk_agent.store_approved_trade", new_callable=AsyncMock, return_value=10),
            patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock),
        ):
            result = await self.agent.process_signal(pool, 1, broker)

        # / max_pct=0.08, strength=1.0, equity=100k, price=100 -> raw qty = 80
        # / total_position_value=20k, new_position_value=80*100=8k
        # / total_exposure = (20k+8k)/100k = 0.28 > 0.25
        # / available = 0.25*100k - 20k = 5k, qty = 5k/100 = 50
        assert result["status"] == "approved"
        assert result["qty"] == 50

    @pytest.mark.asyncio
    async def test_portfolio_risk_reject(self):
        # / existing positions already at 25% limit
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row()
        pool = _mock_pool(mock_conn)

        positions = [_make_position("MSFT", market_value=25000.0)]
        broker = _make_broker(
            balance=_make_balance(equity=100000.0),
            positions=positions,
            price=100.0,
        )

        with patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock):
            result = await self.agent.process_signal(pool, 1, broker)

        assert result["status"] == "rejected"
        assert result["reason"] == "portfolio_risk_exceeded"

    @pytest.mark.asyncio
    async def test_signal_status_updated(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row()
        pool = _mock_pool(mock_conn)
        broker = _make_broker()

        with (
            patch("src.agents.risk_agent.store_approved_trade", new_callable=AsyncMock, return_value=10),
            patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock) as mock_update,
        ):
            await self.agent.process_signal(pool, 1, broker)

        # / last call should be marking signal as processed
        calls = mock_update.call_args_list
        # / find the 'processed' call
        processed_calls = [c for c in calls if c.args[3] == "processed" or c.kwargs.get("status") == "processed"]
        assert len(processed_calls) >= 1


# ---------------------------------------------------------------------------
# / copula tests
# ---------------------------------------------------------------------------

class TestCopulaCheck:
    def setup_method(self):
        self.agent = RiskAgent(max_position_pct=0.08, max_portfolio_risk=0.50)

    @pytest.mark.asyncio
    async def test_copula_skip_small_portfolio(self):
        # / < 5 positions, copula should not be called
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row()
        pool = _mock_pool(mock_conn)

        positions = [_make_position(f"SYM{i}", 1000.0) for i in range(3)]
        broker = _make_broker(
            balance=_make_balance(equity=100000.0),
            positions=positions,
            price=100.0,
        )

        with (
            patch("src.agents.risk_agent.store_approved_trade", new_callable=AsyncMock, return_value=10),
            patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock),
            patch.object(self.agent, "_check_tail_dependence", new_callable=AsyncMock) as mock_copula,
        ):
            result = await self.agent.process_signal(pool, 1, broker)

        mock_copula.assert_not_called()
        assert result["status"] == "approved"

    @pytest.mark.asyncio
    async def test_copula_tail_dep_sizes_down(self):
        # / >= 5 positions, tail_dep > 0.30 -> qty halved
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row(strength=1.0)
        pool = _mock_pool(mock_conn)

        positions = [_make_position(f"SYM{i}", 1000.0) for i in range(5)]
        broker = _make_broker(
            balance=_make_balance(equity=100000.0),
            positions=positions,
            price=100.0,
        )

        with (
            patch("src.agents.risk_agent.store_approved_trade", new_callable=AsyncMock, return_value=10),
            patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock),
            patch.object(self.agent, "_check_tail_dependence", new_callable=AsyncMock, return_value=0.50),
        ):
            result = await self.agent.process_signal(pool, 1, broker)

        assert result["status"] == "approved"
        # / raw qty = (100000*0.08*1.0)/100 = 80
        # / portfolio check: total_pos=5000, new=80*100=8000, exposure=(5k+8k)/100k=0.13 < 0.50 OK
        # / tail_dep 0.50 > 0.30 -> qty halved: 80//2 = 40
        assert result["qty"] == 40

    @pytest.mark.asyncio
    async def test_copula_exception_fallback(self):
        # / copula raises, continues with size-only check
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row(strength=1.0)
        pool = _mock_pool(mock_conn)

        positions = [_make_position(f"SYM{i}", 1000.0) for i in range(5)]
        broker = _make_broker(
            balance=_make_balance(equity=100000.0),
            positions=positions,
            price=100.0,
        )

        with (
            patch("src.agents.risk_agent.store_approved_trade", new_callable=AsyncMock, return_value=10),
            patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock),
            patch.object(self.agent, "_check_tail_dependence", new_callable=AsyncMock, side_effect=Exception("copula failed")),
        ):
            result = await self.agent.process_signal(pool, 1, broker)

        # / still approved — copula failure doesn't block trade
        assert result["status"] == "approved"
        # / qty should be full (80), not halved
        assert result["qty"] == 80


# ---------------------------------------------------------------------------
# / configuration tests
# ---------------------------------------------------------------------------

class TestRiskAgentConfig:
    def test_default_values(self):
        # / phase 6 step 10: default max_position_pct dropped 0.08 -> 0.04 to fit
        # / more concurrent strategies under the same equity. phase 7 post-
        # / research: dropped further to 0.025 to accommodate 30 open positions.
        agent = RiskAgent()
        assert agent.max_position_pct == 0.025
        assert agent.max_portfolio_risk == 0.25
        assert agent.tail_dep_threshold == 0.30

    def test_custom_values(self):
        agent = RiskAgent(max_position_pct=0.10, max_portfolio_risk=0.30, tail_dep_threshold=0.40)
        assert agent.max_position_pct == 0.10
        assert agent.max_portfolio_risk == 0.30
        assert agent.tail_dep_threshold == 0.40

    def test_max_position_pct_from_env(self):
        with patch.dict(os.environ, {"MAX_POSITION_PCT": "0.12"}):
            agent = RiskAgent()
            assert agent.max_position_pct == 0.12

    def test_max_portfolio_risk_from_env(self):
        with patch.dict(os.environ, {"MAX_PORTFOLIO_RISK": "0.35"}):
            agent = RiskAgent()
            assert agent.max_portfolio_risk == 0.35

    def test_zero_explicit_overrides_default(self):
        agent = RiskAgent(max_position_pct=0.0, max_portfolio_risk=0.0)
        assert agent.max_position_pct == 0.0
        assert agent.max_portfolio_risk == 0.0

    def test_zero_via_risk_limits_dict_overrides_default(self):
        agent = RiskAgent(risk_limits={"max_position_pct": 0.0, "max_portfolio_risk": 0.0})
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAX_POSITION_PCT", None)
            os.environ.pop("MAX_PORTFOLIO_RISK", None)
            agent2 = RiskAgent(risk_limits={"max_position_pct": 0.0, "max_portfolio_risk": 0.0})
            assert agent2.max_position_pct == 0.0
            assert agent2.max_portfolio_risk == 0.0


# ---------------------------------------------------------------------------
# / position sizing math
# ---------------------------------------------------------------------------

class TestPositionSizing:
    def setup_method(self):
        self.agent = RiskAgent(max_position_pct=0.08, max_portfolio_risk=0.50)

    @pytest.mark.asyncio
    async def test_qty_calculation(self):
        # / equity=100k, max_pct=0.08, strength=0.5, price=200
        # / qty = (100000 * 0.08 * 0.5) / 200 = 4000 / 200 = 20
        agent = RiskAgent(max_position_pct=0.08, max_portfolio_risk=0.50)
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row(strength=0.5)
        pool = _mock_pool(mock_conn)
        broker = _make_broker(
            balance=_make_balance(equity=100000.0),
            price=200.0,
        )

        with (
            patch("src.agents.risk_agent.store_approved_trade", new_callable=AsyncMock, return_value=10),
            patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock),
        ):
            result = await self.agent.process_signal(pool, 1, broker)

        assert result["qty"] == 20

    @pytest.mark.asyncio
    async def test_strength_scales_position(self):
        # / higher strength -> larger position
        agent = RiskAgent(max_position_pct=0.08, max_portfolio_risk=0.50)

        results = {}
        for strength in [0.3, 0.7]:
            mock_conn = AsyncMock()
            mock_conn.fetchrow.return_value = _make_signal_row(strength=strength)
            pool = _mock_pool(mock_conn)
            broker = _make_broker(balance=_make_balance(equity=100000.0), price=100.0)

            with (
                patch("src.agents.risk_agent.store_approved_trade", new_callable=AsyncMock, return_value=10),
                patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock),
            ):
                result = await agent.process_signal(pool, 1, broker)
                results[strength] = result["qty"]

        assert results[0.7] > results[0.3]

    @pytest.mark.asyncio
    async def test_default_strength_when_none(self):
        # / if signal strength is None, defaults to 0.5
        mock_conn = AsyncMock()
        signal_row = _make_signal_row(strength=0.5)
        signal_row["strength"] = None  # / explicitly None
        mock_conn.fetchrow.return_value = signal_row
        pool = _mock_pool(mock_conn)
        broker = _make_broker(balance=_make_balance(equity=100000.0), price=100.0)

        with (
            patch("src.agents.risk_agent.store_approved_trade", new_callable=AsyncMock, return_value=10),
            patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock),
        ):
            result = await self.agent.process_signal(pool, 1, broker)

        # / with default strength 0.5: qty = (100k * 0.08 * 0.5) / 100 = 40
        assert result["status"] == "approved"
        assert result["qty"] == 40

    @pytest.mark.asyncio
    async def test_approved_trade_stores_strategy_id(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row(strategy_id="evolved_42")
        pool = _mock_pool(mock_conn)
        broker = _make_broker()

        with (
            patch("src.agents.risk_agent.store_approved_trade", new_callable=AsyncMock, return_value=10) as mock_store,
            patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock),
        ):
            await self.agent.process_signal(pool, 1, broker)

        call_kwargs = mock_store.call_args.kwargs
        assert call_kwargs["strategy_id"] == "evolved_42"

    @pytest.mark.asyncio
    async def test_copula_tail_dep_at_threshold_no_sizing(self):
        # / tail_dep exactly at threshold (0.30) should NOT trigger size down
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row(strength=1.0)
        pool = _mock_pool(mock_conn)

        positions = [_make_position(f"SYM{i}", 1000.0) for i in range(5)]
        broker = _make_broker(
            balance=_make_balance(equity=100000.0),
            positions=positions,
            price=100.0,
        )

        agent = RiskAgent(max_position_pct=0.08, max_portfolio_risk=0.50, tail_dep_threshold=0.30)

        with (
            patch("src.agents.risk_agent.store_approved_trade", new_callable=AsyncMock, return_value=10),
            patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock),
            patch.object(agent, "_check_tail_dependence", new_callable=AsyncMock, return_value=0.30),
        ):
            result = await agent.process_signal(pool, 1, broker)

        # / 0.30 is not > 0.30, so no sizing down
        assert result["qty"] == 80


# ---------------------------------------------------------------------------
# / risk limit enforcement tests
# ---------------------------------------------------------------------------

class TestRiskLimitEnforcement:
    def setup_method(self):
        self.agent = RiskAgent(
            max_position_pct=0.08,
            max_portfolio_risk=0.50,
            risk_limits={
                "min_cash_reserve_pct": 0.10,
                "max_daily_trades": 20,
                "max_open_positions": 15,
            },
        )

    @pytest.mark.asyncio
    async def test_reject_cash_reserve_insufficient(self):
        # / cash < 10% of equity → reject
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row()
        pool = _mock_pool(mock_conn)
        broker = _make_broker(balance=_make_balance(equity=100000.0, cash=5000.0))

        with (
            patch("src.agents.risk_agent.get_strategy_positions", new_callable=AsyncMock, return_value=[]),
            patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock),
        ):
            result = await self.agent.process_signal(pool, 1, broker)

        assert result["status"] == "rejected"
        assert result["reason"] == "cash_reserve_insufficient"

    @pytest.mark.asyncio
    async def test_reject_max_positions(self):
        # / 15 open positions → reject new buy
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row()
        pool = _mock_pool(mock_conn)
        positions = [_make_position(f"SYM{i}", 1000.0) for i in range(15)]
        broker = _make_broker(
            balance=_make_balance(equity=100000.0),
            positions=positions, price=100.0,
        )

        with (
            patch("src.agents.risk_agent.get_strategy_positions", new_callable=AsyncMock, return_value=[]),
            patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock),
        ):
            result = await self.agent.process_signal(pool, 1, broker)

        assert result["status"] == "rejected"
        assert "max_positions" in result["reason"]

    @pytest.mark.asyncio
    async def test_reject_max_daily_trades(self):
        # / 20 trades today → reject new buy
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row()
        pool = _mock_pool(mock_conn)
        broker = _make_broker(balance=_make_balance(equity=100000.0))

        with (
            patch("src.agents.risk_agent.get_strategy_positions", new_callable=AsyncMock, return_value=[]),
            patch("src.agents.risk_agent.count_today_approved_trades", new_callable=AsyncMock, return_value=20),
            patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock),
        ):
            result = await self.agent.process_signal(pool, 1, broker)

        assert result["status"] == "rejected"
        assert "max_daily_trades" in result["reason"]

    @pytest.mark.asyncio
    async def test_reject_cross_strategy_concentration(self):
        # / symbol already held at 2x limit → reject. call order:
        # / 1) already_holding check (strat own qty on symbol) -> []
        # / 2) per-strategy slot/exposure check (strat_001 open positions) -> []
        # / 3) cross-strategy concentration (all strategies holding symbol) -> other_positions
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = _make_signal_row(symbol="AAPL")
        pool = _mock_pool(mock_conn)
        broker = _make_broker(balance=_make_balance(equity=100000.0), price=100.0)

        # / 2x limit with new max_position_pct=0.025: 0.025 * 2 * 100000 = 5000.
        # / held 200 shares @ 100 = 20000 by a DIFFERENT strategy, so the
        # / per-strategy cap for strat_001 passes (empty) but the cross-
        # / strategy cap catches the concentration.
        other_positions = [{"strategy_id": "other", "symbol": "AAPL", "qty": 200.0, "avg_entry_price": 95.0}]

        with (
            patch("src.agents.risk_agent.get_strategy_positions", new_callable=AsyncMock, side_effect=[[], [], other_positions]),
            patch("src.agents.risk_agent.count_today_approved_trades", new_callable=AsyncMock, return_value=0),
            patch("src.agents.risk_agent.update_trade_status", new_callable=AsyncMock),
        ):
            result = await self.agent.process_signal(pool, 1, broker)

        assert result["status"] == "rejected"
        assert "cross_strategy_concentration" in result["reason"]

    @pytest.mark.asyncio
    async def test_risk_limits_loaded_from_file(self):
        # / RiskAgent loads risk_limits.json when risk_limits kwarg not passed.
        # / values updated after per-strategy breadth research: raised daily
        # / trades 20->50, open positions 15->30, cash reserve 10%->20% to
        # / match the new 30x2.5%=75% deployed capital target.
        agent = RiskAgent()
        assert agent._min_cash_reserve_pct == 0.10
        assert agent._max_daily_trades == 50
        assert agent._max_open_positions == 30
        assert agent._max_daily_trades_per_strategy == 4
        assert agent._max_positions_per_strategy == 4
        assert agent._max_exposure_per_strategy_pct == 0.08

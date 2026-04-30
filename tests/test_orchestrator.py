# / tests for agent orchestrator

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.orchestrator import (
    ANALYST_MARKET_HOURS,
    ANALYST_OFF_HOURS,
    AgentOrchestrator,
)

# ---------------------------------------------------------------------------
# / helpers
# ---------------------------------------------------------------------------

def _mock_pool():
    pool = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = AsyncMock()
    mock_ctx.__aexit__.return_value = False
    pool.acquire.return_value = mock_ctx
    return pool


def _make_strategy(strategy_id: str = "strat_001", status: str = "live") -> MagicMock:
    strat = MagicMock()
    strat.strategy_id = strategy_id
    strat.name = f"test_{strategy_id}"
    strat.config = {"metadata": {"status": status}}
    return strat


# ---------------------------------------------------------------------------
# / initialization tests
# ---------------------------------------------------------------------------

class TestOrchestratorInit:
    def test_mode_property(self):
        # / verify mode returns constructor arg
        orch = AgentOrchestrator(mode="paper")
        assert orch.mode == "paper"

    def test_mode_property_live(self):
        orch = AgentOrchestrator(mode="live")
        assert orch.mode == "live"

    def test_strategy_pool_property(self):
        # / verify pool is accessible
        orch = AgentOrchestrator()
        pool = orch.strategy_pool
        assert pool is not None
        assert pool.size == 0

    # / every loop name in orchestrator.start()._tasks — keep in sync or tests hang
    _ALL_LOOPS = [
        "_analyst_loop", "_deepseek_loop", "_reasoner_loop", "_strategy_loop",
        "_risk_poll_loop", "_executor_poll_loop", "_evolution_loop",
        "_insider_backfill_loop", "_fundamentals_backfill_loop", "_crypto_backfill_loop",
        "_intraday_backfill_loop", "_daily_bar_backfill_loop", "_price_refresh_loop",
        "_alpaca_sync_loop", "_strategy_metrics_loop", "_alternative_data_loop",
        "_monitoring_loop", "_cost_flush_loop", "_macro_backfill_loop",
        "_alert_loop", "_regime_loop",
        # / phase 2 knowledge base loops
        "_wiki_embedding_loop", "_wiki_archive_loop",
        # / phase 5 step 3 hydration loop
        "_knowledge_hydration_loop",
        # / phase 6 step 1 dashboard trigger poll
        "_trigger_poll_loop",
        # / phase 6 step 10 kelly allocator
        "_capital_allocator_loop",
        # / phase 7 tier 1 stream aggregator
        "_stream_aggregator_loop",
    ]

    def _loop_mocks(self, orch):
        # / patch.multiple bundles all loops + stream startup into a single with-item
        patches = {name: AsyncMock() for name in self._ALL_LOOPS}
        patches["_start_streams"] = AsyncMock()
        return patch.multiple(orch, **patches)

    @pytest.mark.asyncio
    async def test_start_initializes_all_resources(self):
        # / verify init_db, BrokerFactory, load_all_configs called
        orch = AgentOrchestrator(mode="paper")

        mock_pool = _mock_pool()
        mock_strat = _make_strategy("s1", "paper_trading")

        with (
            patch("src.agents.orchestrator.init_db", new_callable=AsyncMock, return_value=mock_pool) as m_init_db,
            patch("src.agents.orchestrator.BrokerFactory") as m_broker_factory,
            patch("src.agents.orchestrator.load_all_configs", return_value=[mock_strat]) as m_load,
            patch("src.agents.orchestrator.sync_trades_from_alpaca", new_callable=AsyncMock, return_value=0),
            patch("src.agents.orchestrator.sync_strategy_positions_from_alpaca", new_callable=AsyncMock, return_value=0),
            patch("src.agents.orchestrator.backfill_trade_pnl", new_callable=AsyncMock, return_value=0),
            self._loop_mocks(orch),
        ):
            await orch.start()

        m_init_db.assert_called_once()
        m_broker_factory.assert_called_once_with(mode="paper")
        m_load.assert_called_once()

    @pytest.mark.asyncio
    async def test_strategies_loaded_on_start(self):
        # / verify load_all_configs called and strategies added to pool
        orch = AgentOrchestrator(mode="paper")

        s1 = _make_strategy("s1", "paper_trading")
        s2 = _make_strategy("s2", "live")

        with (
            patch("src.agents.orchestrator.init_db", new_callable=AsyncMock, return_value=_mock_pool()),
            patch("src.agents.orchestrator.BrokerFactory"),
            patch("src.agents.orchestrator.load_all_configs", return_value=[s1, s2]) as m_load,
            patch("src.agents.orchestrator.sync_trades_from_alpaca", new_callable=AsyncMock, return_value=0),
            patch("src.agents.orchestrator.sync_strategy_positions_from_alpaca", new_callable=AsyncMock, return_value=0),
            patch("src.agents.orchestrator.backfill_trade_pnl", new_callable=AsyncMock, return_value=0),
            self._loop_mocks(orch),
        ):
            await orch.start()

        m_load.assert_called_once_with(
            status_filter={"backtest_pending", "paper_trading", "live"},
        )
        assert orch.strategy_pool.size == 2


# ---------------------------------------------------------------------------
# / stop tests
# ---------------------------------------------------------------------------

class TestOrchestratorStop:
    @pytest.mark.asyncio
    async def test_stop_sets_event_and_cancels_tasks(self):
        # / verify stop_event.set() and task.cancel() called
        orch = AgentOrchestrator()

        # / create fake tasks
        mock_task_1 = MagicMock(spec=asyncio.Task)
        mock_task_2 = MagicMock(spec=asyncio.Task)
        orch._tasks = [mock_task_1, mock_task_2]

        with patch("src.agents.orchestrator.close_db", new_callable=AsyncMock) as m_close:
            # / asyncio.gather needs to be awaitable
            with patch("asyncio.gather", new_callable=AsyncMock):
                await orch.stop()

        assert orch._stop_event.is_set()
        mock_task_1.cancel.assert_called_once()
        mock_task_2.cancel.assert_called_once()
        m_close.assert_called_once()


# ---------------------------------------------------------------------------
# / symbol resolution tests
# ---------------------------------------------------------------------------

class TestGetSymbols:
    def test_get_symbols_from_env(self):
        # / TRADE_SYMBOLS env var -> list
        orch = AgentOrchestrator()
        with patch.dict("os.environ", {"TRADE_SYMBOLS": "AAPL, MSFT, GOOGL"}):
            symbols = orch._get_symbols()
        assert symbols == ["AAPL", "MSFT", "GOOGL"]

    def test_get_symbols_default(self):
        # / no env var -> FULL_UNIVERSE
        orch = AgentOrchestrator()
        with patch.dict("os.environ", {}, clear=True):
            # / make sure TRADE_SYMBOLS is not set
            import os
            os.environ.pop("TRADE_SYMBOLS", None)
            symbols = orch._get_symbols()
        from src.data.symbols import FULL_UNIVERSE
        assert symbols == FULL_UNIVERSE

    def test_get_symbols_strips_whitespace(self):
        orch = AgentOrchestrator()
        with patch.dict("os.environ", {"TRADE_SYMBOLS": " AAPL , ,  TSLA "}):
            symbols = orch._get_symbols()
        assert symbols == ["AAPL", "TSLA"]


# ---------------------------------------------------------------------------
# / market hours tests
# ---------------------------------------------------------------------------

class TestMarketHours:
    def test_is_market_hours_fallback(self):
        # / exchange_calendars import fails, uses simple hour check
        orch = AgentOrchestrator()

        # / mock exchange_calendars to raise ImportError
        with patch.dict("sys.modules", {"exchange_calendars": None}):
            # / set time to 12:00 ET (market hours)
            et = timezone(timedelta(hours=-5))
            mock_now = datetime(2024, 3, 15, 12, 0, 0, tzinfo=et)
            with patch("src.agents.orchestrator.datetime") as m_dt:
                m_dt.now.return_value = mock_now
                m_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = orch._is_market_hours()

            assert result is True

    def test_is_market_hours_fallback_off_hours(self):
        orch = AgentOrchestrator()

        with patch.dict("sys.modules", {"exchange_calendars": None}):
            et = timezone(timedelta(hours=-5))
            mock_now = datetime(2024, 3, 15, 20, 0, 0, tzinfo=et)
            with patch("src.agents.orchestrator.datetime") as m_dt:
                m_dt.now.return_value = mock_now
                m_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = orch._is_market_hours()

            assert result is False


# ---------------------------------------------------------------------------
# / wait_or_stop tests
# ---------------------------------------------------------------------------

class TestWaitOrStop:
    @pytest.mark.asyncio
    async def test_stop_breaks_wait(self):
        # / verify _wait_or_stop returns True when stop_event set
        orch = AgentOrchestrator()
        orch._stop_event.set()
        result = await orch._wait_or_stop(9999)
        assert result is True

    @pytest.mark.asyncio
    async def test_wait_returns_false_on_timeout(self):
        # / timeout expires normally -> returns False
        orch = AgentOrchestrator()
        result = await orch._wait_or_stop(0.01)
        assert result is False


# ---------------------------------------------------------------------------
# / agent loop tests
# ---------------------------------------------------------------------------

@pytest.mark.timeout(10)
class TestAnalystLoop:
    @pytest.mark.asyncio
    async def test_analyst_loop_runs_and_waits(self):
        # / mock analyst.run, verify called then waits
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()

        call_count = 0

        # / phase 9: orchestrator now passes batching kwargs (wall_clock_budget_s,
        # / per_symbol_timeout_s, min_refresh_interval_s). accept **kwargs to stay
        # / forward-compatible with future tuning params.
        async def _fake_run(pool, symbols, run_deepseek=False, **kwargs):
            nonlocal call_count
            call_count += 1
            # / stop after first iteration
            orch._stop_event.set()

        orch._analyst.run = _fake_run
        with patch.object(orch, "_is_market_hours", return_value=False):
            await orch._analyst_loop()

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_analyst_loop_uses_market_interval(self):
        # / verify correct interval during market hours
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()

        intervals_seen = []

        async def _fake_wait(seconds):
            intervals_seen.append(seconds)
            return True  # / stop immediately

        orch._analyst.run = AsyncMock()
        with (
            patch.object(orch, "_is_market_hours", return_value=True),
            patch.object(orch, "_wait_or_stop", side_effect=_fake_wait),
        ):
            await orch._analyst_loop()

        assert intervals_seen[0] == ANALYST_MARKET_HOURS

    @pytest.mark.asyncio
    async def test_analyst_loop_uses_off_hours_interval(self):
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()

        intervals_seen = []

        async def _fake_wait(seconds):
            intervals_seen.append(seconds)
            return True

        orch._analyst.run = AsyncMock()
        with (
            patch.object(orch, "_is_market_hours", return_value=False),
            patch.object(orch, "_wait_or_stop", side_effect=_fake_wait),
        ):
            await orch._analyst_loop()

        assert intervals_seen[0] == ANALYST_OFF_HOURS


@pytest.mark.timeout(10)
class TestStrategyLoop:
    @pytest.mark.asyncio
    async def test_strategy_loop_runs_and_waits(self):
        # / mock strategy.run, verify called then waits
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()
        orch._broker_factory = MagicMock()
        orch._broker_factory.get_broker.return_value = AsyncMock()

        call_count = 0

        async def _fake_run(pool, strategy_pool, broker):
            nonlocal call_count
            call_count += 1
            orch._stop_event.set()

        orch._strategy.run = _fake_run
        with patch.object(orch, "_is_market_hours", return_value=True):
            await orch._strategy_loop()

        assert call_count == 1


@pytest.mark.timeout(10)
class TestRiskPollLoop:
    @pytest.mark.asyncio
    async def test_risk_poll_processes_pending(self):
        # / mock fetch_pending_signals returns 2 signals, verify process_signal called twice
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()
        orch._broker_factory = MagicMock()
        orch._broker_factory.get_broker.return_value = AsyncMock()

        signals = [{"id": 1}, {"id": 2}]
        process_calls = []

        async def _fake_process(pool, signal_id, broker, strategy_pool=None):
            process_calls.append(signal_id)

        orch._risk.process_signal = _fake_process

        with patch("src.agents.orchestrator.fetch_pending_signals", new_callable=AsyncMock, return_value=signals):
            # / stop after first iteration
            async def _stop_after_first(seconds):
                return True

            with patch.object(orch, "_wait_or_stop", side_effect=_stop_after_first):
                await orch._risk_poll_loop()

        assert process_calls == [1, 2]


@pytest.mark.timeout(10)
class TestExecutorPollLoop:
    @pytest.mark.asyncio
    async def test_executor_poll_processes_pending(self):
        # / mock fetch_pending_trades returns 1 trade, verify execute_trade called
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()
        orch._broker_factory = MagicMock()
        orch._broker_factory.get_broker.return_value = AsyncMock()

        trades = [{"id": 42}]
        execute_calls = []

        async def _fake_execute(pool, trade_id, broker, strategy_pool=None):
            execute_calls.append(trade_id)

        orch._executor.execute_trade = _fake_execute

        with patch("src.agents.orchestrator.fetch_pending_trades", new_callable=AsyncMock, return_value=trades):
            async def _stop_after_first(seconds):
                return True

            with patch.object(orch, "_wait_or_stop", side_effect=_stop_after_first):
                await orch._executor_poll_loop()

        assert execute_calls == [42]


# ---------------------------------------------------------------------------
# / error resilience tests
# ---------------------------------------------------------------------------

@pytest.mark.timeout(10)
class TestLoopErrorResilience:
    @pytest.mark.asyncio
    async def test_analyst_loop_error_does_not_crash(self):
        # / mock agent.run raising, loop continues
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()

        call_count = 0

        async def _raise_then_stop(pool, symbols, run_deepseek=False, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("test error")
            orch._stop_event.set()

        orch._analyst.run = _raise_then_stop

        with (
            patch.object(orch, "_is_market_hours", return_value=True),
            patch.object(orch, "_wait_or_stop", new_callable=AsyncMock, return_value=False),
        ):
            await orch._analyst_loop()

        # / should have been called twice (first raises, second stops)
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_risk_poll_error_does_not_crash(self):
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()
        orch._broker_factory = MagicMock()

        call_count = 0

        async def _raise_then_stop(pool, limit=50):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("db error")
            orch._stop_event.set()
            return []

        with (
            patch("src.agents.orchestrator.fetch_pending_signals", side_effect=_raise_then_stop),
            patch.object(orch, "_wait_or_stop", new_callable=AsyncMock, return_value=False),
        ):
            await orch._risk_poll_loop()

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_executor_poll_error_does_not_crash(self):
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()
        orch._broker_factory = MagicMock()

        call_count = 0

        async def _raise_then_stop(pool, limit=50):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("db error")
            orch._stop_event.set()
            return []

        with (
            patch("src.agents.orchestrator.fetch_pending_trades", side_effect=_raise_then_stop),
            patch.object(orch, "_wait_or_stop", new_callable=AsyncMock, return_value=False),
        ):
            await orch._executor_poll_loop()

        assert call_count == 2


# ---------------------------------------------------------------------------
# / evolution loop tests
# ---------------------------------------------------------------------------

@pytest.mark.timeout(10)
class TestEvolutionLoop:
    @pytest.mark.asyncio
    async def test_evolution_loop_calculates_midnight(self):
        # / verify wait time calculation
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()

        wait_seconds_seen = []

        async def _capture_wait(seconds):
            wait_seconds_seen.append(seconds)
            return True  # / stop immediately

        with patch.object(orch, "_wait_or_stop", side_effect=_capture_wait):
            await orch._evolution_loop()

        # / should have waited some positive number of seconds until midnight
        assert len(wait_seconds_seen) == 1
        assert 0 < wait_seconds_seen[0] <= 86400  # / max 24 hours

    @pytest.mark.asyncio
    async def test_evolution_loop_runs_engine_after_wait(self):
        # / verify evolution engine is called when wait expires (not stopped)
        orch = AgentOrchestrator()
        pool = _mock_pool()
        orch._pool = pool
        # / mock strategy pool with 3+ strategies to pass pool size gate
        orch._strategy_pool = MagicMock()
        orch._strategy_pool.size = 10

        call_count = 0
        wait_call_count = 0

        async def _run_once(pool, strategy_pool, market_data=None, regime=None):
            nonlocal call_count
            call_count += 1

        async def _fake_wait(seconds):
            nonlocal wait_call_count
            wait_call_count += 1
            if wait_call_count == 1:
                return False  # / timeout expired, run evolution
            return True  # / stop on second call

        orch._evolution.run = _run_once
        with (
            patch.object(orch, "_wait_or_stop", side_effect=_fake_wait),
            patch.object(orch, "_fetch_evolution_market_data", new_callable=AsyncMock, return_value={}),
        ):
            await orch._evolution_loop()

        assert call_count == 1


# ---------------------------------------------------------------------------
# / deepseek loop tests
# ---------------------------------------------------------------------------

@pytest.mark.timeout(10)
class TestDeepseekLoop:
    @pytest.mark.asyncio
    async def test_deepseek_loop_waits_then_runs(self):
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()

        run_calls = 0
        wait_calls = []

        async def _fake_wait(seconds):
            wait_calls.append(seconds)
            if len(wait_calls) == 1:
                return False  # / first wait expires, run deepseek
            return True  # / stop on second

        async def _fake_run(pool, symbols, run_deepseek=True, **kwargs):
            nonlocal run_calls
            run_calls += 1

        orch._analyst.run = _fake_run
        with patch.object(orch, "_wait_or_stop", side_effect=_fake_wait):
            await orch._deepseek_loop()

        # / first run uses shorter initial delay (120s), not full interval
        assert wait_calls[0] == 120
        assert run_calls == 1

    @pytest.mark.asyncio
    async def test_deepseek_loop_passes_run_deepseek_true(self):
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()

        deepseek_flags = []

        async def _capture_flag(pool, symbols, run_deepseek=True, **kwargs):
            deepseek_flags.append(run_deepseek)

        async def _stop_after_run(seconds):
            return len(deepseek_flags) != 0

        orch._analyst.run = _capture_flag
        with patch.object(orch, "_wait_or_stop", side_effect=_stop_after_run):
            await orch._deepseek_loop()

        assert deepseek_flags == [True]


# ---------------------------------------------------------------------------
# / reasoner loop tests
# ---------------------------------------------------------------------------

@pytest.mark.timeout(10)
class TestReasonerLoop:
    @pytest.mark.asyncio
    async def test_reasoner_loop_calculates_5pm_wait(self):
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()

        wait_seconds_seen = []

        async def _capture_wait(seconds):
            wait_seconds_seen.append(seconds)
            return True  # / stop immediately

        with patch.object(orch, "_wait_or_stop", side_effect=_capture_wait):
            await orch._reasoner_loop()

        assert len(wait_seconds_seen) == 1
        assert 0 < wait_seconds_seen[0] <= 86400

    @pytest.mark.asyncio
    async def test_reasoner_loop_calls_synthesis(self):
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()

        synthesis_called = False
        wait_count = 0

        async def _fake_wait(seconds):
            nonlocal wait_count
            wait_count += 1
            if wait_count == 1:
                return False  # / first wait expires, run synthesis
            return True  # / stop

        async def _fake_synthesis(pool, symbols):
            nonlocal synthesis_called
            synthesis_called = True
            return {"top_buys": [], "top_avoids": [], "portfolio_risk": "low"}

        with (
            patch.object(orch, "_wait_or_stop", side_effect=_fake_wait),
            patch("src.analysis.ai_summary.generate_daily_synthesis", _fake_synthesis),
            patch("src.notifications.notifier.notify_daily_synthesis"),
        ):
            await orch._reasoner_loop()

        assert synthesis_called


# ---------------------------------------------------------------------------
# / regime loop tests (bug 2b: regime backfill must run independently)
# ---------------------------------------------------------------------------

class TestRegimeLoop:
    @pytest.mark.asyncio
    async def test_regime_loop_backfills_equity_and_crypto(self):
        # / bug 2b: _regime_loop must call backfill_regimes for SPY (equity) and BTC-USD (crypto)
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()

        wait_calls = iter([False, True])

        async def _fake_wait(secs):
            return next(wait_calls)

        backfill_mock = AsyncMock(side_effect=[42, 18])

        with (
            patch.object(orch, "_wait_or_stop", side_effect=_fake_wait),
            patch("src.data.regime_detector.backfill_regimes", backfill_mock),
        ):
            await orch._regime_loop()

        assert backfill_mock.call_count == 2
        call_args = [c.args for c in backfill_mock.call_args_list]
        assert call_args[0] == (orch._pool, "SPY", "equity")
        assert call_args[1] == (orch._pool, "BTC-USD", "crypto")

    @pytest.mark.asyncio
    async def test_regime_loop_isolates_errors(self):
        # / failure must not crash the loop — errors are logged + notified, loop continues
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()

        wait_calls = iter([False, True])

        async def _fake_wait(secs):
            return next(wait_calls)

        with (
            patch.object(orch, "_wait_or_stop", side_effect=_fake_wait),
            patch("src.data.regime_detector.backfill_regimes", AsyncMock(side_effect=Exception("db timeout"))),
            patch("src.agents.orchestrator.notify_system_error") as m_notify,
        ):
            await orch._regime_loop()

        m_notify.assert_called_once()
        assert "regime_loop" in m_notify.call_args.args


# ---------------------------------------------------------------------------
# / price refresh loop tests (bug 2a: write to latest_prices, not intraday)
# ---------------------------------------------------------------------------

class TestPriceRefreshLoop:
    @pytest.mark.asyncio
    async def test_price_refresh_calls_store_latest_prices(self):
        # / bug 2a: must call store_latest_prices — NEVER write to market_data_intraday
        # / covers two regression shapes: (a) store_intraday_bars helper and
        # / (b) direct inline conn.execute("INSERT INTO market_data_intraday ...")
        pool = _mock_pool()
        mock_conn = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_conn
        mock_ctx.__aexit__.return_value = False
        pool.acquire.return_value = mock_ctx

        orch = AgentOrchestrator()
        orch._pool = pool

        wait_calls = iter([False, True])

        async def _fake_wait(secs):
            return next(wait_calls)

        store_mock = AsyncMock(return_value=2)
        fetch_mock = AsyncMock(return_value={"AAPL": 175.0, "MSFT": 420.0})

        with (
            patch.object(orch, "_wait_or_stop", side_effect=_fake_wait),
            patch.object(orch, "_is_market_hours", return_value=True),
            patch.object(orch, "_get_symbols", return_value=["AAPL", "MSFT"]),
            patch("src.data.market_data.fetch_latest_prices", fetch_mock),
            patch("src.data.market_data.store_latest_prices", store_mock),
            patch("src.data.market_data.store_intraday_bars", AsyncMock()) as m_intraday,
        ):
            await orch._price_refresh_loop()

        store_mock.assert_called_once()
        m_intraday.assert_not_called()
        # / catch partial revert: no raw SQL touching market_data_intraday either
        for call in mock_conn.execute.call_args_list:
            sql = call.args[0] if call.args else ""
            assert "market_data_intraday" not in sql, (
                f"price refresh must not write to market_data_intraday, got: {sql}"
            )
        for call in mock_conn.executemany.call_args_list:
            sql = call.args[0] if call.args else ""
            assert "market_data_intraday" not in sql


# ---------------------------------------------------------------------------
# / phase 5 step 3: knowledge hydration loop tests
# ---------------------------------------------------------------------------

@pytest.mark.timeout(15)
class TestKnowledgeHydrationLoop:
    @pytest.mark.asyncio
    async def test_no_candidates_does_not_crash(self):
        # / smoke: loop runs with 0 eligible symbols and exits cleanly
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()

        wait_calls = iter([False, False, True])  # / startup delay, then one full cycle, then stop

        async def _fake_wait(secs):
            return next(wait_calls)

        async def _fake_candidates(cap):
            return []

        enrich_called = 0

        async def _fake_enrich(*args, **kwargs):
            nonlocal enrich_called
            enrich_called += 1
            return (None, None)

        with (
            patch.object(orch, "_wait_or_stop", side_effect=_fake_wait),
            patch.object(orch, "_fetch_hydration_candidates", side_effect=_fake_candidates),
            patch("src.agents.orchestrator.log_event", new_callable=AsyncMock),
            patch("src.knowledge.wiki_writer.enrich_symbol_doc", side_effect=_fake_enrich),
        ):
            await orch._knowledge_hydration_loop()

        assert enrich_called == 0

    @pytest.mark.asyncio
    async def test_enriches_exactly_cap_symbols(self):
        # / given 10 eligible symbols but cap=3, enrich_symbol_doc is called exactly 3 times
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()

        wait_calls = iter([False, True])  # / startup passes, single cycle, then stop
        candidates_returned = ["AAPL", "MSFT", "GOOGL"]  # / already truncated by cap

        async def _fake_wait(secs):
            return next(wait_calls)

        async def _fake_candidates(cap):
            assert cap == 3
            return candidates_returned

        async def _fake_bundle(symbol):
            return ([{"date": "2026-01-01"}], {"pe_ratio": 25.0}, [])

        enrich_calls: list[str] = []

        async def _fake_enrich(pool, symbol, analysis_history, fundamentals, insider_trades):
            enrich_calls.append(symbol)
            return (len(enrich_calls), "content")

        with (
            patch.dict("os.environ", {"WIKI_HYDRATION_DAILY_CAP": "3"}),
            patch.object(orch, "_wait_or_stop", side_effect=_fake_wait),
            patch.object(orch, "_fetch_hydration_candidates", side_effect=_fake_candidates),
            patch.object(orch, "_load_hydration_bundle", side_effect=_fake_bundle),
            patch("src.agents.orchestrator.log_event", new_callable=AsyncMock),
            patch("src.knowledge.wiki_writer.enrich_symbol_doc", side_effect=_fake_enrich),
        ):
            await orch._knowledge_hydration_loop()

        assert enrich_calls == ["AAPL", "MSFT", "GOOGL"]

    @pytest.mark.asyncio
    async def test_error_in_one_symbol_does_not_stop_others(self):
        # / if enrich fails for one symbol, loop continues to the next
        orch = AgentOrchestrator()
        orch._pool = _mock_pool()

        wait_calls = iter([False, True])

        async def _fake_wait(secs):
            return next(wait_calls)

        async def _fake_candidates(cap):
            return ["A", "B", "C"]

        async def _fake_bundle(symbol):
            return ([], None, [])

        attempts: list[str] = []

        async def _fake_enrich(pool, symbol, *a, **kw):
            attempts.append(symbol)
            if symbol == "B":
                raise RuntimeError("model 500")
            return (1, "ok")

        with (
            patch.object(orch, "_wait_or_stop", side_effect=_fake_wait),
            patch.object(orch, "_fetch_hydration_candidates", side_effect=_fake_candidates),
            patch.object(orch, "_load_hydration_bundle", side_effect=_fake_bundle),
            patch("src.agents.orchestrator.log_event", new_callable=AsyncMock),
            patch("src.knowledge.wiki_writer.enrich_symbol_doc", side_effect=_fake_enrich),
        ):
            await orch._knowledge_hydration_loop()

        # / all three attempted despite the middle failure
        assert attempts == ["A", "B", "C"]

    def test_hydration_daily_cap_default(self):
        # / env unset -> default cap
        from src.agents.orchestrator import KNOWLEDGE_HYDRATION_DEFAULT_CAP
        orch = AgentOrchestrator()
        with patch.dict("os.environ", {}, clear=True):
            import os as _os
            _os.environ.pop("WIKI_HYDRATION_DAILY_CAP", None)
            assert orch._hydration_daily_cap() == KNOWLEDGE_HYDRATION_DEFAULT_CAP

    def test_hydration_daily_cap_override(self):
        orch = AgentOrchestrator()
        with patch.dict("os.environ", {"WIKI_HYDRATION_DAILY_CAP": "10"}):
            assert orch._hydration_daily_cap() == 10

    def test_hydration_daily_cap_invalid_falls_back(self):
        # / bad env value falls back to default, doesn't crash
        from src.agents.orchestrator import KNOWLEDGE_HYDRATION_DEFAULT_CAP
        orch = AgentOrchestrator()
        with patch.dict("os.environ", {"WIKI_HYDRATION_DAILY_CAP": "not_a_number"}):
            assert orch._hydration_daily_cap() == KNOWLEDGE_HYDRATION_DEFAULT_CAP

    def test_hydration_daily_cap_negative_clamped(self):
        orch = AgentOrchestrator()
        with patch.dict("os.environ", {"WIKI_HYDRATION_DAILY_CAP": "-1"}):
            assert orch._hydration_daily_cap() == 0

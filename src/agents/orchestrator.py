
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pandas as pd
import structlog

from src.agents.alert_engine import check_and_fire as alert_check_and_fire
from src.agents.analyst_agent import AnalystAgent
from src.agents.capital_allocator import compute_allocations
from src.agents.data_tools import fire_and_forget, log_event
from src.agents.executor_agent import ExecutorAgent
from src.agents.market_tools import fetch_latest_regime
from src.agents.position_tools import (
    get_strategy_positions,
    reconcile_strategy_positions,
    sync_strategy_positions_from_alpaca,
)
from src.agents.risk_agent import RiskAgent
from src.agents.strategy_agent import StrategyAgent
from src.agents.stream_manager import StreamManager
from src.agents.sync_tools import backfill_trade_pnl, sync_trades_from_alpaca
from src.agents.trade_tools import (
    fetch_pending_signals,
    fetch_pending_trades,
    update_trade_status,
)
from src.brokers.broker_factory import BrokerFactory
from src.data import loop_registry
from src.data.cost_tracker import flush_to_db
from src.data.db import close_db, init_db
from src.data.fred_macro import fetch_macro_indicators
from src.data.fundamentals import fetch_all_fundamentals, store_fundamentals
from src.data.market_data import (
    aggregate_intraday_to_2h,
    backfill,
    backfill_intraday,
    fetch_latest_prices,
    store_latest_prices,
)
from src.data.regime_detector import (
    backfill_regimes,
    backfill_regimes_per_sector,
    snapshot_regime_daily,
)
from src.data.retention import prune_observation_log, prune_system_events
from src.data.sec_filings import fetch_insider_trades, store_insider_trades
from src.data.symbols import FULL_UNIVERSE, get_sector, is_crypto
from src.evolution.evolution_engine import EvolutionEngine
from src.knowledge.loops import _embed_backfill_once
from src.knowledge.wiki_writer import enrich_symbol_doc
from src.notifications.notifier import notify_system_error
from src.strategies.strategy_loader import load_all_configs
from src.strategies.strategy_pool import StrategyPool

logger = structlog.get_logger(__name__)

# / schedule intervals in seconds
# / batched staleness-ordered runs, time-budgeted
ANALYST_MARKET_HOURS = 1200  # / 20 minutes (3 batches/hour,
ANALYST_OFF_HOURS = 1800         # / 30 minutes (slower off-hours)
ANALYST_BUDGET_S = 420.0  # / 7 min wall-clock budget
ANALYST_MIN_REFRESH_S = 1800.0  # / skip symbols refreshed within
ANALYST_PER_SYMBOL_TIMEOUT_S = 180.0
STRATEGY_MARKET_HOURS = 300      # / 5 minutes
STRATEGY_OFF_HOURS = 300  # / 5 minutes (consistent for
DEEPSEEK_INTERVAL = 1800  # / 30 minutes (dual-llm is
DEEPSEEK_BUDGET_S = 480.0  # / 8 min wall-clock budget
DEEPSEEK_MIN_REFRESH_S = 2700.0  # / skip symbols deepseek'd within
INTRADAY_INTERVAL = 3600         # / 1 hour
RISK_POLL_INTERVAL = 5           # / 5 seconds
EXECUTOR_POLL_INTERVAL = 5       # / 5 seconds
STRATEGY_METRICS_INTERVAL = 3600 # / 1 hour
ALTERNATIVE_DATA_INTERVAL = 86400  # / 24 hours
MONITORING_INTERVAL = 3600         # / 1 hour
COST_FLUSH_INTERVAL = 3600         # / 1 hour
DAILY_BAR_INTERVAL = 14400         # / 4 hours
PRICE_REFRESH_INTERVAL = 300       # / 5 minutes
STREAM_AGGREGATOR_INTERVAL = 60  # / drain buffer + upsert
STREAM_FRESH_TICK_S = 90  # / treat stream as healthy
PRICE_TICK_BROADCAST_MIN_INTERVAL = 1.0  # / cap ws broadcast rate
ALERT_CHECK_INTERVAL = 30  # / 30 seconds — isolated
CRYPTO_BACKFILL_INTERVAL = 1800  # / 30 minutes — crypto
REGIME_LOOP_INTERVAL = 21600  # / 6 hours — regime
KNOWLEDGE_HYDRATION_INTERVAL = 86400  # / 24 hours — daily
KNOWLEDGE_HYDRATION_STARTUP_DELAY = 900  # / 15 min offset so
KNOWLEDGE_HYDRATION_DEFAULT_CAP = 5  # / hard cap on symbols
WIKI_STUB_WORD_THRESHOLD = 150  # / docs below this word
WIKI_MIN_ANALYSIS_ROWS = 5  # / need N recent analyses


class AgentOrchestrator:
    def __init__(self, mode: str = "paper"):
        self._mode = mode
        self._stop_event: asyncio.Event = asyncio.Event()
        self._pool: asyncpg.Pool | None = None
        self._broker_factory: BrokerFactory | None = None
        self._strategy_pool = StrategyPool()
        rl = self._load_risk_limits()
        self._analyst = AnalystAgent()
        self._strategy = StrategyAgent()
        self._risk = RiskAgent(risk_limits=rl)
        self._executor = ExecutorAgent()
        self._evolution = EvolutionEngine(risk_limits=rl)
        self._risk_limits = rl
        self._tasks: list[asyncio.Task] = []
        self._last_drift: dict[str, float] = {}
        self._alert_prev_prices: dict[str, float] = {}
        self._last_equity_regime: str | None = None
        self._last_crypto_regime: str | None = None
        self._streams = StreamManager(broadcast_semaphore_size=50)
        self._service_handlers: dict = {
            "macro_backfill": self._svc_macro_backfill,
            "fundamentals_backfill": self._svc_fundamentals_backfill,
            "insider_backfill": self._svc_insider_backfill,
            "regime_backfill": self._svc_regime_backfill,
            "daily_bar_backfill": self._svc_daily_bar_backfill,
            "intraday_backfill": self._svc_intraday_backfill,
            "crypto_backfill": self._svc_crypto_backfill,
            "price_refresh": self._svc_price_refresh,
            "alternative_data": self._run_alternative_data_registry_cycle,
            "analyst": self._svc_analyst,
            "deepseek": self._svc_deepseek,
            "strategy": self._svc_strategy,
            "strategy_metrics": self._compute_strategy_metrics,
            "wiki_embedding": self._svc_wiki_embedding,
            "knowledge_hydration": self._svc_knowledge_hydration,
            "evolution": self._svc_evolution,
            "cost_flush": self._svc_cost_flush,
            "capital_allocator": self._svc_capital_allocator,
        }

    @staticmethod
    def _load_risk_limits() -> dict:
        from pathlib import Path
        path = Path(__file__).parent.parent.parent / "configs" / "risk_limits.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("risk_limits_load_failed", error=str(exc)[:120])
        return {}

    def _broker(self):
        # / set in start()
        assert self._broker_factory is not None
        return self._broker_factory.get_broker()

    async def start(self) -> None:
        logger.info("orchestrator_starting", mode=self._mode)
        await self._bootstrap_db()
        await self._bootstrap_alpaca_sync()
        self._bootstrap_broker()
        self._bootstrap_strategies()
        await self._bootstrap_kronos()
        await self._start_streams()
        self._spawn_loops()
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("orchestrator_tasks_cancelled")

    async def _bootstrap_db(self) -> None:
        self._pool = await init_db()
        try:
            await prune_system_events(self._pool, max_age_days=30)
            await prune_observation_log(self._pool, max_age_days=14)
        except Exception as exc:
            logger.warning("retention_prune_failed", error=str(exc)[:120])

    async def _bootstrap_alpaca_sync(self) -> None:
        assert self._pool is not None
        try:
            async with self._pool.acquire() as conn:
                cleaned = await conn.execute(
                    """DELETE FROM trade_log WHERE broker = 'PaperBroker'
                    OR (broker IS NULL AND order_id ~ '^[0-9a-f]{8}-')"""
                )
                if cleaned != "DELETE 0":
                    logger.info("cleaned_stale_paper_trades", result=cleaned)
            synced = await sync_trades_from_alpaca(self._pool)
            if synced:
                logger.info("startup_alpaca_sync", trades_synced=synced)
            pos_synced = await sync_strategy_positions_from_alpaca(self._pool)
            if pos_synced:
                logger.info("startup_position_sync", positions_synced=pos_synced)
            backfilled = await backfill_trade_pnl(self._pool)
            if backfilled:
                logger.info("startup_pnl_backfill", updated=backfilled)
        except Exception:
            # / swallow startup sync error
            logger.debug("startup_sync_failed", exc_info=True)

    def _bootstrap_broker(self) -> None:
        self._broker_factory = BrokerFactory(mode=self._mode)

    def _bootstrap_strategies(self) -> None:
        strategies = load_all_configs(
            status_filter={"backtest_pending", "paper_trading", "promoted", "live"},
        )
        for strat in strategies:
            status = "promoted"
            if hasattr(strat, "config") and strat.config.get("metadata", {}).get("status"):
                status = strat.config["metadata"]["status"]
            self._strategy_pool.add(strat, status=status)
        logger.info(
            "orchestrator_initialized",
            strategies=self._strategy_pool.size,
            mode=self._mode,
        )

    async def _bootstrap_kronos(self) -> None:
        try:
            from src.quant.kronos_signal import ensure_loaded_and_record_status
            kronos_status = await ensure_loaded_and_record_status(self._pool)
            logger.info(
                "kronos_startup_status",
                **{k: v for k, v in kronos_status.items() if k != "fallback_reason"},
            )
        except Exception as exc:
            logger.warning("kronos_startup_record_failed", error=str(exc)[:200])

    def _spawn_loops(self) -> None:
        self._tasks = [
            asyncio.create_task(self._analyst_loop(), name="analyst"),
            asyncio.create_task(self._deepseek_loop(), name="deepseek"),
            asyncio.create_task(self._reasoner_loop(), name="reasoner"),
            asyncio.create_task(self._strategy_loop(), name="strategy"),
            asyncio.create_task(self._risk_poll_loop(), name="risk"),
            asyncio.create_task(self._executor_poll_loop(), name="executor"),
            asyncio.create_task(self._evolution_loop(), name="evolution"),
            asyncio.create_task(self._insider_backfill_loop(), name="insider_backfill"),
            asyncio.create_task(self._fundamentals_backfill_loop(), name="fundamentals_backfill"),
            asyncio.create_task(self._crypto_backfill_loop(), name="crypto_backfill"),
            asyncio.create_task(self._intraday_backfill_loop(), name="intraday_backfill"),
            asyncio.create_task(self._daily_bar_backfill_loop(), name="daily_bar_backfill"),
            asyncio.create_task(self._price_refresh_loop(), name="price_refresh"),
            asyncio.create_task(self._alpaca_sync_loop(), name="alpaca_sync"),
            asyncio.create_task(self._strategy_metrics_loop(), name="strategy_metrics"),
            asyncio.create_task(self._alternative_data_loop(), name="alternative_data"),
            asyncio.create_task(self._monitoring_loop(), name="monitoring"),
            asyncio.create_task(self._cost_flush_loop(), name="cost_flush"),
            asyncio.create_task(self._macro_backfill_loop(), name="macro_backfill"),
            asyncio.create_task(self._alert_loop(), name="alert"),
            asyncio.create_task(self._regime_loop(), name="regime_backfill"),
            asyncio.create_task(self._wiki_embedding_loop(), name="wiki_embedding"),
            asyncio.create_task(self._wiki_archive_loop(), name="wiki_archive"),
            asyncio.create_task(self._knowledge_hydration_loop(), name="knowledge_hydration"),
            asyncio.create_task(self._trigger_poll_loop(), name="trigger_poll"),
            asyncio.create_task(self._capital_allocator_loop(), name="capital_allocator"),
            asyncio.create_task(self._stream_aggregator_loop(), name="stream_aggregator"),
        ]

    async def stop(self) -> None:
        # / graceful shutdown
        logger.info("orchestrator_stopping")
        self._stop_event.set()

        # / cancel all tasks
        for task in self._tasks:
            task.cancel()

        await self._streams.stop()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        try:
            await self._executor.tasks.drain(timeout=10)
        except Exception as exc:
            logger.debug("executor_task_drain_failed", error=str(exc)[:120])

        try:
            from src.data.alpaca_client import close_alpaca_client
            from src.data.llm_client import close_llm_clients
            from src.data.resilience import close_http_client
            await close_http_client()
            await close_llm_clients()
            await close_alpaca_client()
        except Exception as exc:
            logger.debug("http_client_shutdown_failed", error=str(exc)[:100])

        # / close db
        await close_db()
        logger.info("orchestrator_stopped")

    @property
    def strategy_pool(self) -> StrategyPool:
        return self._strategy_pool

    @property
    def mode(self) -> str:
        return self._mode

    def _get_symbols(self) -> list[str]:
        symbols_env = os.environ.get("TRADE_SYMBOLS")
        if symbols_env:
            return [s.strip() for s in symbols_env.split(",") if s.strip()]
        return FULL_UNIVERSE

    @staticmethod
    def _et_tz():
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo("America/New_York")
        except (ImportError, KeyError):
            return timezone(timedelta(hours=-5))

    async def _sleep_until_et_hour(self, hour: int):
        et = self._et_tz()
        now = datetime.now(et)
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait = (target - now).total_seconds()
        return await self._wait_or_stop(wait), target

    def _is_market_hours(self) -> bool:
        try:
            import exchange_calendars as xcals
            import pandas as pd

            nyse = xcals.get_calendar("XNYS")
            now = pd.Timestamp.now(tz="America/New_York")

            if not nyse.is_session(now.normalize()):
                return False

            session_open = nyse.session_open(now.normalize())
            session_close = nyse.session_close(now.normalize())
            return session_open <= now <= session_close
        except (ImportError, ValueError, KeyError, AttributeError):
            now = datetime.now(self._et_tz())
            return 9 <= now.hour < 16

    async def _wait_or_stop(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
            return True  # / stop event was set
        except asyncio.TimeoutError:
            return False  # / timeout expired normally

    async def _analyst_loop(self) -> None:
        timeout = loop_registry.timeout_for("analyst")
        while not self._stop_event.is_set():
            interval = ANALYST_MARKET_HOURS if self._is_market_hours() else ANALYST_OFF_HOURS
            try:
                async with loop_registry.track(self._pool, "analyst"):
                    symbols = self._get_symbols()
                    await asyncio.wait_for(
                        self._analyst.run(
                            self._pool, symbols,
                            run_deepseek=False,
                            wall_clock_budget_s=ANALYST_BUDGET_S,
                            per_symbol_timeout_s=ANALYST_PER_SYMBOL_TIMEOUT_S,
                            min_refresh_interval_s=ANALYST_MIN_REFRESH_S,
                        ),
                        timeout=timeout,
                    )
                    try:
                        from src.dashboard.app import _ws_clients, broadcast
                        if _ws_clients:
                            fire_and_forget(broadcast("analysis_update", {"cycle": "complete"}))
                    except Exception as exc:
                        logger.debug("broadcast_analysis_failed", error=str(exc)[:100])
            except asyncio.TimeoutError:
                logger.warning("analyst_loop_timeout", timeout_s=timeout)
            except Exception as exc:
                logger.error("analyst_loop_error", exc_info=True)
                notify_system_error(str(exc), "analyst_loop")

            if await self._wait_or_stop(interval):
                break

    async def _deepseek_loop(self) -> None:
        timeout = loop_registry.timeout_for("deepseek")
        first_run = True
        while not self._stop_event.is_set():
            wait = 120 if first_run else DEEPSEEK_INTERVAL
            first_run = False
            if await self._wait_or_stop(wait):
                break
            try:
                async with loop_registry.track(self._pool, "deepseek"):
                    symbols = self._get_symbols()
                    await asyncio.wait_for(
                        self._analyst.run(
                            self._pool, symbols,
                            run_deepseek=True,
                            wall_clock_budget_s=DEEPSEEK_BUDGET_S,
                            per_symbol_timeout_s=ANALYST_PER_SYMBOL_TIMEOUT_S,
                            min_refresh_interval_s=DEEPSEEK_MIN_REFRESH_S,
                        ),
                        timeout=timeout,
                    )
                    logger.info("deepseek_cycle_complete")
            except asyncio.TimeoutError:
                logger.warning("deepseek_loop_timeout", timeout_s=timeout)
            except Exception as exc:
                logger.error("deepseek_loop_error", exc_info=True)
                notify_system_error(str(exc), "deepseek_loop")

    async def _reasoner_loop(self) -> None:
        from src.analysis.ai_summary import generate_daily_synthesis
        from src.notifications.notifier import notify_daily_synthesis
        while not self._stop_event.is_set():
            stopped, target = await self._sleep_until_et_hour(17)

            logger.info("reasoner_waiting", next_run=str(target))

            if stopped:
                break

            try:
                async with loop_registry.track(self._pool, "reasoner"):
                    symbols = self._get_symbols()
                    result = await generate_daily_synthesis(self._pool, symbols)
                    if result:
                        portfolio = None
                        try:
                            broker = self._broker()
                            account = await broker.get_account_balance()
                            positions = await broker.get_positions()
                            portfolio = {
                                "value": account.portfolio_value,
                                "daily_pnl": 0,
                                "positions": len(positions),
                                "strategies": self._strategy_pool.size,
                            }
                        except Exception as exc:
                            logger.warning("portfolio_fetch_for_synthesis_failed", error=str(exc))
                        notify_daily_synthesis(result, portfolio=portfolio)
                    logger.info("reasoner_synthesis_complete")
            except Exception as exc:
                logger.error("reasoner_loop_error", exc_info=True)
                notify_system_error(str(exc), "reasoner_loop")

    async def _strategy_loop(self) -> None:
        while not self._stop_event.is_set():
            interval = STRATEGY_MARKET_HOURS if self._is_market_hours() else STRATEGY_OFF_HOURS
            try:
                async with loop_registry.track(self._pool, "strategy"):
                    broker = self._broker()
                    await self._strategy.run(
                        self._pool, self._strategy_pool, broker,
                    )
                    try:
                        from src.dashboard.app import _ws_clients, broadcast
                        if _ws_clients:
                            fire_and_forget(broadcast("strategy_update", {"cycle": "complete"}))
                    except Exception as exc:
                        logger.debug("broadcast_strategy_failed", error=str(exc)[:100])
            except Exception as exc:
                logger.error("strategy_loop_error", exc_info=True)
                notify_system_error(str(exc), "strategy_loop")

            if await self._wait_or_stop(interval):
                break

    async def _risk_poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "risk"):
                    pending = await fetch_pending_signals(self._pool)
                    for signal in pending:
                        try:
                            broker = self._broker()
                            result = await self._risk.process_signal(
                                self._pool, signal["id"], broker,
                                strategy_pool=self._strategy_pool,
                            )
                            if result.get("status") not in ("approved", "skipped"):
                                logger.info(
                                    "risk_signal_result",
                                    signal_id=signal["id"],
                                    symbol=signal.get("symbol"),
                                    result=result.get("status"),
                                    reason=result.get("reason"),
                                )
                        except Exception as exc:
                            logger.error("risk_signal_error", signal_id=signal["id"], error=str(exc))
                            try:
                                await update_trade_status(
                                    self._pool, "trade_signals", signal["id"], "error",
                                )
                            except Exception as inner:
                                logger.warning(
                                    "risk_poll_status_update_failed",
                                    signal_id=signal["id"], error=str(inner)[:120],
                                )
            except Exception:
                # / swallow loop tick error
                logger.error("risk_poll_error", exc_info=True)

            if await self._wait_or_stop(RISK_POLL_INTERVAL):
                break

    async def _executor_poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "executor"):
                    pending = await fetch_pending_trades(self._pool)
                    for trade in pending:
                        broker = self._broker()
                        await self._executor.execute_trade(
                            self._pool, trade["id"], broker, strategy_pool=self._strategy_pool,
                        )
            except Exception:
                # / swallow loop tick error
                logger.error("executor_poll_error", exc_info=True)

            if await self._wait_or_stop(EXECUTOR_POLL_INTERVAL):
                break

    async def _evolution_loop(self) -> None:
        while not self._stop_event.is_set():
            stopped, target = await self._sleep_until_et_hour(0)

            logger.info("evolution_waiting", next_run=str(target))

            if stopped:
                break

            try:
                async with loop_registry.track(self._pool, "evolution"):
                    if self._strategy_pool.size < 3:
                        logger.info("evolution_skipped_small_pool", pool_size=self._strategy_pool.size)
                    else:
                        market_data = await self._fetch_evolution_market_data()
                        current_regime = await fetch_latest_regime(self._pool, "equity")
                        await self._evolution.run(
                            self._pool, self._strategy_pool,
                            market_data=market_data, regime=current_regime,
                        )
            except Exception as exc:
                logger.error("evolution_loop_error", exc_info=True)
                notify_system_error(str(exc), "evolution_loop")

    async def _insider_backfill_loop(self) -> None:
        while not self._stop_event.is_set():
            stopped, target = await self._sleep_until_et_hour(6)

            logger.info("insider_backfill_waiting", next_run=str(target))

            if stopped:
                break

            try:
                async with loop_registry.track(self._pool, "insider_backfill"):
                    from src.data.sec_filings import fetch_insider_trades, store_insider_trades
                    from src.data.symbols import get_sector
                    symbols = [s for s in self._get_symbols() if not is_crypto(s) and get_sector(s) != "etfs"]
                    total_trades = 0
                    for symbol in symbols:
                        try:
                            trades = await fetch_insider_trades(symbol)
                            if trades:
                                await store_insider_trades(self._pool, trades)
                                total_trades += len(trades)
                        except Exception as exc:
                            logger.warning("insider_backfill_symbol_error", symbol=symbol, error=str(exc))
                    await log_event(
                        self._pool, "info", "insider_backfill",
                        f"symbols={len(symbols)} trades={total_trades}",
                    )
            except Exception as exc:
                logger.error("insider_backfill_error", exc_info=True)
                notify_system_error(str(exc), "insider_backfill")
                await log_event(self._pool, "error", "insider_backfill", str(exc)[:200])

    async def _fundamentals_backfill_loop(self) -> None:
        while not self._stop_event.is_set():
            stopped, target = await self._sleep_until_et_hour(7)

            logger.info("fundamentals_backfill_waiting", next_run=str(target))

            if stopped:
                break

            try:
                async with loop_registry.track(self._pool, "fundamentals_backfill"):
                    from src.data.fundamentals import fetch_all_fundamentals, store_fundamentals
                    symbols = [s for s in self._get_symbols() if not is_crypto(s)]
                    data = await fetch_all_fundamentals(symbols)
                    if data:
                        await store_fundamentals(self._pool, data)
                        logger.info("fundamentals_backfill_complete", count=len(data))
                    await log_event(
                        self._pool, "info", "fundamentals_backfill",
                        f"symbols={len(symbols)} updated={len(data) if data else 0}",
                    )
            except Exception as exc:
                logger.error("fundamentals_backfill_error", exc_info=True)
                notify_system_error(str(exc), "fundamentals_backfill")
                await log_event(self._pool, "error", "fundamentals_backfill", str(exc)[:200])

    async def _crypto_backfill_loop(self) -> None:
        if await self._wait_or_stop(120):
            return
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "crypto_backfill"):
                    from src.data.crypto_data import fetch_coin_data
                    from src.data.market_data import backfill
                    crypto_symbols = [s for s in self._get_symbols() if is_crypto(s)]
                    if crypto_symbols:
                        results = await backfill(self._pool, crypto_symbols, years=1)
                        total = sum(results.values())
                        if total:
                            logger.info("crypto_bar_backfill_complete", symbols=len(crypto_symbols), bars=total)
                        for symbol in crypto_symbols:
                            try:
                                data = await fetch_coin_data(symbol)
                                if data and self._pool:
                                    await log_event(
                                        self._pool, "info", "crypto_backfill",
                                        f"mcap={data.get('market_cap')}, vol={data.get('total_volume')}",
                                        symbol=symbol,
                                    )
                            except Exception as exc:
                                logger.warning("crypto_metadata_error", symbol=symbol, error=str(exc))
            except Exception as exc:
                logger.error("crypto_backfill_error", exc_info=True)
                notify_system_error(str(exc), "crypto_backfill")

            if await self._wait_or_stop(CRYPTO_BACKFILL_INTERVAL):
                break

    async def _regime_loop(self) -> None:
        if await self._wait_or_stop(180):
            return
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "regime_backfill"):
                    from src.data.regime_detector import (
                        backfill_regimes,
                        backfill_regimes_per_sector,
                        snapshot_regime_daily,
                    )
                    equity_count = await backfill_regimes(self._pool, "SPY", "equity")
                    crypto_count = await backfill_regimes(self._pool, "BTC-USD", "crypto")
                    try:
                        sector_counts = await backfill_regimes_per_sector(self._pool)
                        sector_total = sum(sector_counts.values())
                    except Exception as exc:
                        logger.warning("sector_regime_backfill_failed", error=str(exc)[:200])
                        sector_total = 0
                    logger.info(
                        "regime_backfill_complete",
                        equity_rows=equity_count,
                        crypto_rows=crypto_count,
                        sector_rows=sector_total,
                    )
                    await log_event(
                        self._pool, "info", "regime_backfill",
                        f"equity={equity_count} crypto={crypto_count} sectors={sector_total}",
                    )
                    await self._check_regime_shift("equity")
                    await self._check_regime_shift("crypto")
                    try:
                        equity_regime = await fetch_latest_regime(self._pool, "equity")
                        crypto_regime = await fetch_latest_regime(self._pool, "crypto")
                        if equity_regime:
                            await snapshot_regime_daily(self._pool, "equity", equity_regime)
                        if crypto_regime:
                            await snapshot_regime_daily(self._pool, "crypto", crypto_regime)
                    except Exception as exc:
                        logger.warning("regime_snapshot_failed", error=str(exc)[:200])
            except Exception as exc:
                logger.error("regime_loop_error", exc_info=True)
                notify_system_error(str(exc), "regime_loop")
                await log_event(self._pool, "error", "regime_backfill", str(exc)[:200])

            if await self._wait_or_stop(REGIME_LOOP_INTERVAL):
                break

    async def _check_regime_shift(self, market: str) -> None:
        try:
            latest = await fetch_latest_regime(self._pool, market)
        except (asyncpg.PostgresError, KeyError, AttributeError):
            return
        if latest is None:
            return
        attr = "_last_equity_regime" if market == "equity" else "_last_crypto_regime"
        previous = getattr(self, attr)
        if previous is None:
            setattr(self, attr, latest)
            return
        if latest == previous:
            return
        # / transition detected
        setattr(self, attr, latest)
        logger.info("regime_shift_detected", market=market, old=previous, new=latest)
        try:
            from src.knowledge.regime_wiki import on_regime_shift
            await on_regime_shift(self._pool, previous, latest, None, market)
        except Exception as exc:
            logger.warning("regime_shift_write_failed", market=market, error=str(exc)[:200])

    async def _intraday_backfill_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "intraday_backfill"):
                    from src.data.market_data import aggregate_intraday_to_2h, backfill_intraday
                    symbols = self._get_symbols()
                    results_1h = await backfill_intraday(self._pool, symbols, days=10, timeframe="1Hour")
                    total_1h = sum(results_1h.values())
                    results_2h = await aggregate_intraday_to_2h(self._pool, symbols, days=10)
                    total_2h = sum(results_2h.values())
                    missing = [s for s, n in results_1h.items() if n == 0]
                    if missing:
                        logger.warning("intraday_backfill_symbol_empty", count=len(missing), sample=missing[:10])
                    logger.info(
                        "intraday_backfill_complete",
                        symbols=len(symbols), bars_1h=total_1h, bars_2h=total_2h,
                    )
                    await log_event(
                        self._pool, "info", "intraday_backfill",
                        f"1h={total_1h} 2h={total_2h} empty={len(missing)}",
                    )
            except Exception as exc:
                logger.error("intraday_backfill_error", exc_info=True)
                notify_system_error(str(exc), "intraday_backfill")
                await log_event(self._pool, "error", "intraday_backfill", str(exc)[:200])

            if await self._wait_or_stop(INTRADAY_INTERVAL):
                break

    async def _daily_bar_backfill_loop(self) -> None:
        if await self._wait_or_stop(120):
            return
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "daily_bar_backfill"):
                    from src.data.market_data import backfill
                    symbols = self._get_symbols()
                    results = await backfill(self._pool, symbols, years=1)
                    total = sum(results.values())
                    if total:
                        logger.info("daily_bar_backfill_complete", symbols=len(symbols), bars=total)
                    await log_event(
                        self._pool, "info", "daily_bar_backfill", f"symbols={len(symbols)} bars={total}",
                    )
            except Exception as exc:
                logger.error("daily_bar_backfill_error", exc_info=True)
                notify_system_error(str(exc), "daily_bar_backfill")
                await log_event(self._pool, "error", "daily_bar_backfill", str(exc)[:200])

            if await self._wait_or_stop(DAILY_BAR_INTERVAL):
                break

    async def _price_refresh_loop(self) -> None:
        if await self._wait_or_stop(60):
            return
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "price_refresh"):
                    if not self._is_market_hours():
                        pass
                    else:
                        from src.data.market_data import fetch_latest_prices, store_latest_prices
                        symbols = self._get_symbols()
                        equity_healthy = self._streams.is_equity_healthy()
                        crypto_healthy = self._streams.is_crypto_healthy()

                        poll_syms: list[str] = []
                        for s in symbols:
                            if is_crypto(s):
                                if not crypto_healthy:
                                    poll_syms.append(s)
                            else:
                                if (not equity_healthy
                                        or s not in self._streams.streamed_equity_symbols):
                                    poll_syms.append(s)

                        if not poll_syms:
                            logger.debug("price_refresh_streams_cover_all_skip")
                        else:
                            prices = await fetch_latest_prices(poll_syms)
                            if prices:
                                stored = await store_latest_prices(self._pool, prices)
                                reason = ("streams_unhealthy"
                                          if not (equity_healthy and crypto_healthy)
                                          else "overflow_symbols")
                                logger.info("price_refresh_poll_complete",
                                            symbols=stored, reason=reason)
                                await log_event(
                                    self._pool, "info", "price_refresh",
                                    f"symbols={stored} reason={reason}",
                                )
            except Exception as exc:
                logger.warning("price_refresh_error", error=str(exc)[:100])
                notify_system_error(f"price refresh failed: {str(exc)[:80]}", "price_refresh")
                await log_event(self._pool, "error", "price_refresh", str(exc)[:200])

            if await self._wait_or_stop(PRICE_REFRESH_INTERVAL):
                break

    async def _start_streams(self) -> None:
        await self._streams.start(self._get_symbols())

    async def _stream_aggregator_loop(self) -> None:
        if self._streams.tick_buffer is None:
            return
        while not self._stop_event.is_set():
            await self._streams.aggregate_once(self._pool)
            if await self._wait_or_stop(STREAM_AGGREGATOR_INTERVAL):
                break

    async def _alpaca_sync_loop(self) -> None:
        while not self._stop_event.is_set():
            async with loop_registry.track(self._pool, "alpaca_sync"):
                try:
                    synced = await sync_trades_from_alpaca(self._pool)
                    if synced:
                        logger.info("alpaca_periodic_sync", trades_synced=synced)
                except Exception:
                    # / swallow alpaca sync error
                    logger.debug("alpaca_sync_error", exc_info=True)

                await self._alpaca_reconcile_positions()

            # / sync every 5 minutes
            if await self._wait_or_stop(300):
                break

    async def _alpaca_reconcile_positions(self) -> None:
        try:
            all_positions = await get_strategy_positions(self._pool)
            tracked: dict[str, float] = {}
            for p in all_positions:
                tracked[p["symbol"]] = tracked.get(p["symbol"], 0) + p["qty"]

            broker = self._broker()
            alpaca_positions = await broker.get_positions()
            alpaca_map: dict[str, float] = {p.symbol: p.qty for p in alpaca_positions}
            alpaca_prices: dict[str, float] = {
                p.symbol: float(p.avg_entry_price or 0) for p in alpaca_positions
            }

            drift_found = False
            current_drift: dict[str, float] = {}

            for symbol, alpaca_qty in alpaca_map.items():
                tracked_qty = tracked.pop(symbol, 0)
                if abs(tracked_qty - alpaca_qty) > 0.0001:
                    current_drift[symbol] = alpaca_qty
                    logger.warning("position_drift", symbol=symbol, tracked=tracked_qty, alpaca=alpaca_qty)
                    if symbol not in self._last_drift:
                        notify_system_error(f"position drift: {symbol} tracked={tracked_qty} alpaca={alpaca_qty}", "reconciliation")
                    drift_found = True

            for symbol, tracked_qty in tracked.items():
                if tracked_qty > 0.0001:
                    current_drift[symbol] = 0
                    logger.warning("position_drift", symbol=symbol, tracked=tracked_qty, alpaca=0)
                    if symbol not in self._last_drift:
                        notify_system_error(f"position closed externally: {symbol} (was {tracked_qty})", "reconciliation")
                    drift_found = True

            self._last_drift = current_drift

            if drift_found:
                await reconcile_strategy_positions(
                    self._pool, alpaca_map, full_sync=True, price_map=alpaca_prices,
                )
                logger.info("position_reconciliation_auto_fixed")
            else:
                logger.debug("position_reconciliation_ok", symbols=len(alpaca_map))
        except Exception:
            # / swallow reconcile error
            logger.warning("position_reconciliation_error", exc_info=True)

    async def _macro_backfill_loop(self) -> None:
        while not self._stop_event.is_set():
            stopped, target = await self._sleep_until_et_hour(9)
            logger.info("macro_backfill_waiting", next_run=str(target))
            if stopped:
                break
            try:
                async with loop_registry.track(self._pool, "macro_backfill"):
                    from src.data.fred_macro import fetch_macro_indicators
                    indicators = await fetch_macro_indicators(self._pool)
                    if indicators:
                        logger.info("macro_backfill_complete", indicators=len(indicators))
                    await log_event(
                        self._pool, "info", "macro_backfill",
                        f"indicators={len(indicators) if indicators else 0}",
                    )
            except Exception as exc:
                logger.error("macro_backfill_error", exc_info=True)
                notify_system_error(str(exc), "macro_backfill")
                await log_event(self._pool, "error", "macro_backfill", str(exc)[:200])

    async def _alert_loop(self) -> None:
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "alert"):
                    broker = self._broker() if self._broker_factory else None
                    if broker is not None:
                        ws_broadcast = None
                        try:
                            from src.dashboard.app import _ws_clients
                            from src.dashboard.app import broadcast as ws_broadcast_fn
                            if _ws_clients:
                                ws_broadcast = ws_broadcast_fn
                        except ImportError:
                            ws_broadcast = None
                        await alert_check_and_fire(
                            self._pool, broker, ws_broadcast, webhook_url,
                            self._alert_prev_prices,
                        )
                        await log_event(self._pool, "info", "alert", "cycle_ok")
            except Exception as exc:
                logger.error("alert_loop_error", exc_info=True)
                notify_system_error(str(exc), "alert_loop")
                await log_event(self._pool, "error", "alert", str(exc)[:200])

            if await self._wait_or_stop(ALERT_CHECK_INTERVAL):
                break

    async def _alternative_data_loop(self, use_registry: bool = True) -> None:
        if await self._wait_or_stop(300):
            return
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "alternative_data"):
                    if use_registry:
                        await self._run_alternative_data_registry_cycle()
                    else:
                        await self._run_alternative_data_legacy_cycle()
            except Exception as exc:
                logger.error("alternative_data_error", exc_info=True)
                notify_system_error(str(exc), "alternative_data")
                await log_event(self._pool, "error", "alternative_data", str(exc)[:200])
            if await self._wait_or_stop(ALTERNATIVE_DATA_INTERVAL):
                break

    async def _run_alternative_data_registry_cycle(self) -> None:
        from src.data.source_registry import AltDataSource, all_sources

        sources = all_sources()
        by_name_deduped: dict[str, AltDataSource] = {}
        for src in sources:
            by_name_deduped.setdefault(src.name, src)

        for src in by_name_deduped.values():
            if not src.is_global:
                continue
            try:
                await src.fetch(self._pool)
            except Exception as exc:
                logger.warning("alt_data_global_fetch_failed", source=src.name, error=str(exc)[:120])

        # / per-symbol sources
        symbols = [s for s in self._get_symbols() if not is_crypto(s)]
        per_symbol_sources = [s for s in by_name_deduped.values() if not s.is_global]

        for symbol in symbols:
            is_etf = get_sector(symbol) == "etfs"
            for src in per_symbol_sources:
                if is_etf and src.skip_etfs:
                    continue
                try:
                    if src.name == "dark_pool":
                        data = await src.fetch(symbol, pool=self._pool)
                    else:
                        data = await src.fetch(symbol)
                    if not data:
                        continue
                    if src.store_needs_symbol:
                        await src.store(self._pool, symbol, data)
                    else:
                        await src.store(self._pool, data)
                except Exception as exc:
                    logger.warning(
                        "alt_data_source_error",
                        source=src.name, symbol=symbol, error=str(exc)[:120],
                    )
            await asyncio.sleep(2)  # / throttle api calls
        logger.info("alternative_data_backfill_complete", count=len(symbols), via="registry")
        await log_event(
            self._pool, "info", "alternative_data", f"symbols={len(symbols)} via=registry",
        )

    async def _run_alternative_data_legacy_cycle(self) -> None:
        # / deprecated path
        from src.data.analyst_ratings import fetch_analyst_ratings, store_analyst_ratings
        from src.data.congressional_trades import fetch_congressional_trades, store_congressional_trades
        from src.data.dark_pool import fetch_dark_pool_data, store_dark_pool
        from src.data.earnings_revisions import fetch_earnings_estimates, store_earnings_estimates
        from src.data.options_data import fetch_options_data, store_options_data
        from src.data.short_interest import fetch_short_interest, store_short_interest

        symbols = [s for s in self._get_symbols() if not is_crypto(s)]
        for symbol in symbols:
            is_etf = get_sector(symbol) == "etfs"
            try:
                if not is_etf:
                    # / analyst ratings
                    ratings = await fetch_analyst_ratings(symbol)
                    if ratings:
                        await store_analyst_ratings(self._pool, symbol, ratings)
                    # / earnings revisions
                    estimates = await fetch_earnings_estimates(symbol)
                    if estimates:
                        await store_earnings_estimates(self._pool, estimates)
                    # / congressional trades
                    trades = await fetch_congressional_trades(symbol)
                    if trades:
                        await store_congressional_trades(self._pool, trades)
                    # / options
                    options = await fetch_options_data(symbol)
                    if options:
                        await store_options_data(self._pool, options)
                si = await fetch_short_interest(symbol)
                if si:
                    await store_short_interest(self._pool, si)
                dp = await fetch_dark_pool_data(symbol, pool=self._pool)
                if dp:
                    await store_dark_pool(self._pool, dp)
            except Exception as exc:
                logger.warning("alt_data_symbol_error", symbol=symbol, error=str(exc))
            await asyncio.sleep(2)  # / throttle api calls
        logger.info("alternative_data_backfill_complete", count=len(symbols), via="legacy")
        await log_event(
            self._pool, "info", "alternative_data", f"symbols={len(symbols)} via=legacy",
        )

    async def _monitoring_loop(self) -> None:
        if await self._wait_or_stop(600):
            return
        while not self._stop_event.is_set():
            async with loop_registry.track(self._pool, "monitoring"):
                # / data staleness check
                try:
                    from src.data.staleness_monitor import check_all_freshness
                    stale = await check_all_freshness(self._pool)
                    stale_sources = [s for s in stale if s.is_stale]
                    if stale_sources:
                        msg = ", ".join(f"{s.source} ({s.staleness_hours:.0f}h)" for s in stale_sources)
                        logger.warning("stale_data_sources", sources=msg)
                        notify_system_error(f"stale data: {msg}", "staleness_monitor")
                except Exception as exc:
                    logger.warning("staleness_check_error", error=str(exc))

                # / strategy decay check
                try:
                    from src.analysis.strategy_decay import check_all_strategies
                    decay_signals = await check_all_strategies(self._pool, self._strategy_pool)
                    for ds in decay_signals:
                        logger.warning("strategy_decay_detected",
                            strategy_id=ds.strategy_id,
                            recommendation=ds.recommendation,
                            rolling_sharpe=ds.rolling_sharpe,
                        )
                        if ds.recommendation == "kill":
                            notify_system_error(
                                f"strategy {ds.strategy_id} decay: kill recommended (sharpe={ds.rolling_sharpe:.2f})",
                                "strategy_decay",
                            )
                except Exception as exc:
                    logger.warning("decay_check_error", error=str(exc))

                # / portfolio correlation check
                try:
                    from src.quant.correlation_monitor import check_portfolio_correlation
                    broker = self._broker()
                    positions = await broker.get_positions()
                    if len(positions) >= 2:
                        alert = await check_portfolio_correlation(self._pool, positions)
                        if alert and alert.is_concentrated:
                            logger.warning("portfolio_concentrated",
                                avg_corr=alert.avg_correlation,
                                max_pair=alert.max_pair,
                            )
                            notify_system_error(
                                f"portfolio concentrated: avg_corr={alert.avg_correlation:.2f}",
                                "correlation_monitor",
                            )
                except Exception as exc:
                    logger.warning("correlation_check_error", error=str(exc))

            if await self._wait_or_stop(MONITORING_INTERVAL):
                break

    async def _cost_flush_loop(self) -> None:
        if await self._wait_or_stop(COST_FLUSH_INTERVAL):
            return
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "cost_flush"):
                    from src.data.cost_tracker import flush_to_db
                    flushed = await flush_to_db(self._pool)
                    if flushed:
                        logger.info("cost_tracker_flushed", rows=flushed)
            except Exception as exc:
                logger.warning("cost_flush_error", error=str(exc))
            if await self._wait_or_stop(COST_FLUSH_INTERVAL):
                break

    async def _fetch_evolution_market_data(self) -> dict[str, pd.DataFrame]:
        assert self._pool is not None
        symbols = self._get_symbols()
        market_data: dict[str, pd.DataFrame] = {}
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT symbol, date, open, high, low, close, volume
                    FROM market_data
                    WHERE symbol = ANY($1::text[])
                    ORDER BY symbol, date ASC""",
                    symbols,
                )
        except Exception as exc:
            logger.warning("evolution_market_data_query_failed", error=str(exc))
            return market_data
        by_symbol: dict[str, list[dict]] = {}
        for r in rows:
            by_symbol.setdefault(r["symbol"], []).append(r)
        for symbol, sym_rows in by_symbol.items():
            if len(sym_rows) < 50:
                continue
            df = pd.DataFrame(
                [{
                    "open": float(r["open"]) if r["open"] else 0,
                    "high": float(r["high"]) if r["high"] else 0,
                    "low": float(r["low"]) if r["low"] else 0,
                    "close": float(r["close"]) if r["close"] else 0,
                    "volume": int(r["volume"]) if r["volume"] else 0,
                } for r in sym_rows],
                index=pd.DatetimeIndex([r["date"] for r in sym_rows]),
            )
            market_data[symbol] = df
        logger.info("evolution_market_data_loaded", symbols=len(market_data))
        return market_data

    async def _strategy_metrics_loop(self) -> None:
        if await self._wait_or_stop(60):
            return
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "strategy_metrics"):
                    await self._compute_strategy_metrics()
            except Exception as exc:
                logger.error("strategy_metrics_loop_error", exc_info=True)
                notify_system_error(str(exc), "strategy_metrics_loop")
            if await self._wait_or_stop(STRATEGY_METRICS_INTERVAL):
                break

    async def _compute_strategy_metrics(self) -> None:
        from src.analysis.live_strategy_metrics import compute_live_strategy_metrics
        updated = await compute_live_strategy_metrics(self._pool)
        logger.info("strategy_metrics_computed", strategies=updated)


    async def _wiki_periodic(self, name: str, inner, initial_wait: int, interval: int) -> None:
        if await self._wait_or_stop(initial_wait):
            return
        timeout = loop_registry.timeout_for(name)
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, name):
                    await asyncio.wait_for(inner(self._pool), timeout=timeout)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                logger.warning(f"{name}_pass_timeout", timeout_s=timeout)
            except Exception as exc:
                logger.error(f"{name}_loop_error", error=str(exc)[:200])
                notify_system_error(str(exc), f"{name}_loop")
            if await self._wait_or_stop(interval):
                return

    async def _wiki_embedding_loop(self) -> None:
        from src.knowledge.loops import wiki_embedding_backfill_loop
        await self._wiki_periodic("wiki_embedding", wiki_embedding_backfill_loop, 300, 6 * 3600)

    async def _wiki_archive_loop(self) -> None:
        from src.knowledge.loops import wiki_archive_loop
        await self._wiki_periodic("wiki_archive", wiki_archive_loop, 600, 24 * 3600)


    def _hydration_daily_cap(self) -> int:
        raw = os.environ.get("WIKI_HYDRATION_DAILY_CAP")
        if not raw:
            return KNOWLEDGE_HYDRATION_DEFAULT_CAP
        try:
            cap = int(raw)
        except ValueError:
            logger.warning("wiki_hydration_cap_parse_failed", raw=raw)
            return KNOWLEDGE_HYDRATION_DEFAULT_CAP
        return max(0, cap)

    async def _fetch_hydration_candidates(self, cap: int) -> list[str]:
        if cap <= 0 or self._pool is None:
            return []
        symbols = self._get_symbols()
        if not symbols:
            return []
        try:
            async with self._pool.acquire() as conn:
                stub_rows = await conn.fetch(
                    """
                    SELECT unnest(symbols) AS symbol, updated_at
                    FROM wiki_documents
                    WHERE category = 'symbols'
                      AND (confidence = 'seed' OR word_count < $1)
                    ORDER BY updated_at ASC
                    """,
                    WIKI_STUB_WORD_THRESHOLD,
                )
                stub_symbols = [r["symbol"] for r in stub_rows if r["symbol"] in symbols]
                have_docs = {r["symbol"] for r in stub_rows}
                for sym in symbols:
                    if sym not in have_docs and sym not in stub_symbols:
                        stub_symbols.append(sym)

                if not stub_symbols:
                    return []
                analysis_rows = await conn.fetch(
                    """
                    SELECT symbol, COUNT(*) AS n
                    FROM analysis_scores
                    WHERE symbol = ANY($1::varchar[])
                      AND date >= CURRENT_DATE - INTERVAL '7 days'
                    GROUP BY symbol
                    """,
                    stub_symbols,
                )
                eligible = {r["symbol"] for r in analysis_rows if r["n"] >= WIKI_MIN_ANALYSIS_ROWS}
        except Exception as exc:
            logger.warning("wiki_hydration_candidate_fetch_failed", error=str(exc)[:200])
            return []

        picks: list[str] = []
        for sym in stub_symbols:
            if sym in eligible and sym not in picks:
                picks.append(sym)
                if len(picks) >= cap:
                    break
        return picks

    async def _load_hydration_bundle(
        self, symbol: str,
    ) -> tuple[list[dict], dict | None, list[dict]]:
        assert self._pool is not None
        analysis_rows: list[dict] = []
        fundamentals: dict | None = None
        insider: list[dict] = []
        async with self._pool.acquire() as conn:
            try:
                rows = await conn.fetch(
                    """
                    SELECT date, fundamental_score, technical_score, composite_score,
                           regime, regime_confidence
                    FROM analysis_scores
                    WHERE symbol = $1
                      AND date >= CURRENT_DATE - INTERVAL '30 days'
                    ORDER BY date DESC
                    """,
                    symbol,
                )
                analysis_rows = [dict(r) for r in rows]
            except Exception as exc:
                logger.info("wiki_hydration_analysis_fetch_failed",
                            symbol=symbol, error=str(exc)[:120])

            try:
                frow = await conn.fetchrow(
                    """
                    SELECT pe_ratio, pe_forward, ps_ratio, peg_ratio,
                           revenue_growth_1y, revenue_growth_3y, fcf_margin,
                           debt_to_equity, sector, sector_pe_avg
                    FROM fundamentals
                    WHERE symbol = $1
                    ORDER BY date DESC LIMIT 1
                    """,
                    symbol,
                )
                fundamentals = dict(frow) if frow else None
            except Exception as exc:
                logger.info("wiki_hydration_fundamentals_fetch_failed",
                            symbol=symbol, error=str(exc)[:120])

            try:
                irows = await conn.fetch(
                    """
                    SELECT filing_date, insider_name, insider_title, transaction_type,
                           shares, price_per_share, total_value
                    FROM insider_trades
                    WHERE symbol = $1
                      AND filing_date >= CURRENT_DATE - INTERVAL '90 days'
                    ORDER BY filing_date DESC
                    """,
                    symbol,
                )
                insider = [dict(r) for r in irows]
            except Exception as exc:
                logger.info("wiki_hydration_insider_fetch_failed",
                            symbol=symbol, error=str(exc)[:120])

        return analysis_rows, fundamentals, insider

    async def _knowledge_hydration_loop(self) -> None:
        if await self._wait_or_stop(KNOWLEDGE_HYDRATION_STARTUP_DELAY):
            return
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "knowledge_hydration"):
                    cap = self._hydration_daily_cap()
                    picks = await self._fetch_hydration_candidates(cap)
                    if not picks:
                        logger.info("wiki_hydration_no_candidates", cap=cap)
                    else:
                        from src.knowledge.wiki_writer import enrich_symbol_doc
                        enriched = 0
                        for symbol in picks:
                            try:
                                bundle = await self._load_hydration_bundle(symbol)
                                analysis_rows, fundamentals, insider = bundle
                                doc_id, _ = await enrich_symbol_doc(
                                    self._pool, symbol, analysis_rows, fundamentals, insider,
                                )
                                if doc_id is not None:
                                    enriched += 1
                            except Exception as exc:
                                logger.warning(
                                    "wiki_hydration_symbol_failed",
                                    symbol=symbol, error=str(exc)[:200],
                                )
                        logger.info(
                            "wiki_hydration_cycle_complete",
                            candidates=len(picks), enriched=enriched, cap=cap,
                        )
                        await log_event(
                            self._pool, "info", "knowledge_hydration",
                            f"candidates={len(picks)} enriched={enriched} cap={cap}",
                        )
            except Exception as exc:
                logger.error("knowledge_hydration_loop_error", exc_info=True)
                notify_system_error(str(exc), "knowledge_hydration_loop")

            if await self._wait_or_stop(KNOWLEDGE_HYDRATION_INTERVAL):
                break


    async def _capital_allocator_loop(self) -> None:
        if await self._wait_or_stop(3600):
            return
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "capital_allocator"):
                    from src.agents.capital_allocator import compute_allocations, get_dynamic_caps
                    caps = get_dynamic_caps(self._risk_limits)
                    allocs = await compute_allocations(self._pool, max_position_pct=caps.per_position_pct)
                    await log_event(
                        self._pool, "info", "capital_allocator",
                        f"strategies={len(allocs)} active={caps.active_count} max_pct={caps.per_position_pct}",
                    )
            except Exception as exc:
                logger.error("capital_allocator_error", exc_info=True)
                notify_system_error(str(exc), "capital_allocator")

            # / refresh weekly
            if await self._wait_or_stop(604800):
                break


    async def _trigger_poll_loop(self) -> None:
        if await self._wait_or_stop(10):
            return
        while not self._stop_event.is_set():
            try:
                claimed = await loop_registry.claim_pending_triggers(self._pool)
                for trigger_id, service in claimed:
                    fire_and_forget(self._run_trigger(trigger_id, service))
            except Exception as exc:
                logger.warning("trigger_poll_error", error=str(exc)[:120])
            if await self._wait_or_stop(5):
                break

    async def _run_trigger(self, trigger_id: int, service: str) -> None:
        logger.info("trigger_received", service=service, trigger_id=trigger_id)
        if await loop_registry.is_loop_running(self._pool, service):
            logger.info("trigger_skipped_already_running", service=service, trigger_id=trigger_id)
            await loop_registry.complete_trigger(
                self._pool, trigger_id, "skipped", "already_running",
            )
            return
        try:
            async with loop_registry.track(self._pool, service):
                await self._run_service_once(service)
            await loop_registry.complete_trigger(self._pool, trigger_id, "done")
        except Exception as exc:
            logger.error("trigger_run_failed", service=service, error=str(exc)[:200])
            await loop_registry.complete_trigger(self._pool, trigger_id, "error", str(exc))

    async def _run_service_once(self, service: str) -> None:
        handler = self._service_handlers.get(service)
        if handler is None:
            raise ValueError(f"service not triggerable: {service}")
        await handler()

    async def _svc_macro_backfill(self) -> None:
        await fetch_macro_indicators(self._pool)

    async def _svc_fundamentals_backfill(self) -> None:
        syms = [s for s in self._get_symbols() if not is_crypto(s)]
        data = await fetch_all_fundamentals(syms)
        if data:
            await store_fundamentals(self._pool, data)

    async def _svc_insider_backfill(self) -> None:
        syms = [s for s in self._get_symbols() if not is_crypto(s) and get_sector(s) != "etfs"]
        for sym in syms:
            try:
                trades = await fetch_insider_trades(sym)
                if trades:
                    await store_insider_trades(self._pool, trades)
            except Exception as exc:
                logger.warning("trigger_insider_symbol_failed", symbol=sym, error=str(exc)[:120])

    async def _svc_regime_backfill(self) -> None:
        await backfill_regimes(self._pool, "SPY", "equity")
        await backfill_regimes(self._pool, "BTC-USD", "crypto")
        try:
            await backfill_regimes_per_sector(self._pool)
        except Exception as exc:
            logger.warning("trigger_sector_regime_failed", error=str(exc)[:120])
        try:
            eq = await fetch_latest_regime(self._pool, "equity")
            cr = await fetch_latest_regime(self._pool, "crypto")
            if eq:
                await snapshot_regime_daily(self._pool, "equity", eq)
            if cr:
                await snapshot_regime_daily(self._pool, "crypto", cr)
        except Exception as exc:
            logger.warning("trigger_regime_snapshot_failed", error=str(exc)[:120])

    async def _svc_daily_bar_backfill(self) -> None:
        await backfill(self._pool, self._get_symbols(), years=1)

    async def _svc_intraday_backfill(self) -> None:
        symbols = self._get_symbols()
        await backfill_intraday(self._pool, symbols, days=10, timeframe="1Hour")
        await aggregate_intraday_to_2h(self._pool, symbols, days=10)

    async def _svc_crypto_backfill(self) -> None:
        crypto = [s for s in self._get_symbols() if is_crypto(s)]
        if crypto:
            await backfill(self._pool, crypto, years=1)

    async def _svc_price_refresh(self) -> None:
        prices = await fetch_latest_prices(self._get_symbols())
        if prices:
            await store_latest_prices(self._pool, prices)

    async def _svc_analyst(self) -> None:
        await self._analyst.run(self._pool, self._get_symbols(), run_deepseek=False)

    async def _svc_deepseek(self) -> None:
        await self._analyst.run(self._pool, self._get_symbols(), run_deepseek=True)

    async def _svc_strategy(self) -> None:
        broker = self._broker()
        await self._strategy.run(self._pool, self._strategy_pool, broker)

    async def _svc_wiki_embedding(self) -> None:
        await _embed_backfill_once(self._pool)

    async def _svc_knowledge_hydration(self) -> None:
        cap = self._hydration_daily_cap()
        picks = await self._fetch_hydration_candidates(cap)
        if not picks:
            return
        for sym in picks:
            try:
                bundle = await self._load_hydration_bundle(sym)
                await enrich_symbol_doc(self._pool, sym, *bundle)
            except Exception as exc:
                logger.warning("trigger_hydration_symbol_failed", symbol=sym, error=str(exc)[:120])

    async def _svc_evolution(self) -> None:
        if self._strategy_pool.size < 3:
            return
        market_data = await self._fetch_evolution_market_data()
        regime = await fetch_latest_regime(self._pool, "equity")
        await self._evolution.run(
            self._pool, self._strategy_pool,
            market_data=market_data, regime=regime,
        )

    async def _svc_cost_flush(self) -> None:
        await flush_to_db(self._pool)

    async def _svc_capital_allocator(self) -> None:
        from src.agents.capital_allocator import get_dynamic_caps
        caps = get_dynamic_caps(self._risk_limits)
        await compute_allocations(self._pool, max_position_pct=caps.per_position_pct)

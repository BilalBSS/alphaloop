# / agent orchestrator — coordinates all trading agents on schedule
# / runs analyst, strategy, risk, executor, and evolution loops concurrently
# / uses exchange_calendars for nyse market hours detection

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import structlog

from src.agents import tools
from src.agents.analyst_agent import AnalystAgent
from src.agents.executor_agent import ExecutorAgent
from src.agents.risk_agent import RiskAgent
from src.agents.strategy_agent import StrategyAgent
from src.brokers.broker_factory import BrokerFactory
from src.data.db import close_db, init_db
from src.data.symbols import FULL_UNIVERSE, is_crypto
from src.evolution.evolution_engine import EvolutionEngine
from src.notifications.notifier import notify_system_error
from src.strategies.strategy_loader import load_all_configs
from src.strategies.strategy_pool import StrategyPool

logger = structlog.get_logger(__name__)

# / schedule intervals in seconds
ANALYST_MARKET_HOURS = 1800      # / 30 minutes
ANALYST_OFF_HOURS = 1800         # / 30 minutes (crypto trades 24/7)
STRATEGY_MARKET_HOURS = 300      # / 5 minutes
STRATEGY_OFF_HOURS = 300         # / 5 minutes (consistent for crypto)
DEEPSEEK_INTERVAL = 3600         # / 1 hour
RISK_POLL_INTERVAL = 5           # / 5 seconds
EXECUTOR_POLL_INTERVAL = 5       # / 5 seconds


class AgentOrchestrator:
    def __init__(self, mode: str = "paper"):
        self._mode = mode
        self._stop_event: asyncio.Event = asyncio.Event()
        self._pool = None
        self._broker_factory: BrokerFactory | None = None
        self._strategy_pool = StrategyPool()
        self._analyst = AnalystAgent()
        self._strategy = StrategyAgent()
        self._risk = RiskAgent()
        self._executor = ExecutorAgent()
        self._evolution = EvolutionEngine()
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        # / initialize resources and start all agent loops
        logger.info("orchestrator_starting", mode=self._mode)

        # / init db
        self._pool = await init_db()

        # / init broker
        self._broker_factory = BrokerFactory(mode=self._mode)

        # / load strategy configs
        strategies = load_all_configs(
            status_filter={"backtest_pending", "paper_trading", "live"},
        )
        for strat in strategies:
            status = "live"
            if hasattr(strat, "config") and strat.config.get("metadata", {}).get("status"):
                status = strat.config["metadata"]["status"]
            self._strategy_pool.add(strat, status=status)

        logger.info(
            "orchestrator_initialized",
            strategies=self._strategy_pool.size,
            mode=self._mode,
        )

        # / launch all loops
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
        ]

        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("orchestrator_tasks_cancelled")

    async def stop(self) -> None:
        # / graceful shutdown
        logger.info("orchestrator_stopping")
        self._stop_event.set()

        # / cancel all tasks
        for task in self._tasks:
            task.cancel()

        # / wait for tasks to finish (with timeout)
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

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
        # / get symbols to analyze from environment or default
        symbols_env = os.environ.get("TRADE_SYMBOLS")
        if symbols_env:
            return [s.strip() for s in symbols_env.split(",") if s.strip()]
        return FULL_UNIVERSE

    def _is_market_hours(self) -> bool:
        # / check if nyse is currently open
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
        except Exception:
            # / fallback: simple hour check (9:30-16:00 ET)
            et = timezone(timedelta(hours=-5))
            now = datetime.now(et)
            return 9 <= now.hour < 16

    async def _wait_or_stop(self, seconds: float) -> bool:
        # / wait for interval or stop event, returns True if stopped
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
            return True  # / stop event was set
        except asyncio.TimeoutError:
            return False  # / timeout expired normally

    async def _analyst_loop(self) -> None:
        # / run analyst agent on schedule (groq only, deepseek on separate hourly loop)
        while not self._stop_event.is_set():
            interval = ANALYST_MARKET_HOURS if self._is_market_hours() else ANALYST_OFF_HOURS
            try:
                symbols = self._get_symbols()
                await self._analyst.run(self._pool, symbols, run_deepseek=False)
            except Exception as exc:
                logger.error("analyst_loop_error", exc_info=True)
                notify_system_error(str(exc), "analyst_loop")

            if await self._wait_or_stop(interval):
                break

    async def _deepseek_loop(self) -> None:
        # / run deepseek analysis hourly (separate from groq every-cycle)
        while not self._stop_event.is_set():
            if await self._wait_or_stop(DEEPSEEK_INTERVAL):
                break
            try:
                symbols = self._get_symbols()
                await self._analyst.run(self._pool, symbols, run_deepseek=True)
                logger.info("deepseek_cycle_complete")
            except Exception as exc:
                logger.error("deepseek_loop_error", exc_info=True)
                notify_system_error(str(exc), "deepseek_loop")

    async def _reasoner_loop(self) -> None:
        # / run daily synthesis at 5PM ET via deepseek-reasoner
        from src.analysis.ai_summary import generate_daily_synthesis
        from src.notifications.notifier import notify_daily_synthesis
        while not self._stop_event.is_set():
            # / calculate seconds until 5PM ET
            et = timezone(timedelta(hours=-5))
            now = datetime.now(et)
            target = now.replace(hour=17, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            wait_seconds = (target - now).total_seconds()

            logger.info("reasoner_waiting", next_run=str(target), wait_seconds=wait_seconds)

            if await self._wait_or_stop(wait_seconds):
                break

            try:
                symbols = self._get_symbols()
                result = await generate_daily_synthesis(self._pool, symbols)
                if result:
                    # / fetch portfolio stats for merged synthesis message
                    portfolio = None
                    try:
                        broker = self._broker_factory.get_broker()
                        account = await broker.get_account_balance()
                        positions = await broker.get_positions()
                        portfolio = {
                            "value": account.get("portfolio_value", 0),
                            "daily_pnl": account.get("daily_pnl", 0),
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
        # / run strategy agent on schedule
        while not self._stop_event.is_set():
            interval = STRATEGY_MARKET_HOURS if self._is_market_hours() else STRATEGY_OFF_HOURS
            try:
                broker = self._broker_factory.get_broker()
                await self._strategy.run(
                    self._pool, self._strategy_pool, broker,
                )
            except Exception as exc:
                logger.error("strategy_loop_error", exc_info=True)
                notify_system_error(str(exc), "strategy_loop")

            if await self._wait_or_stop(interval):
                break

    async def _risk_poll_loop(self) -> None:
        # / poll for pending trade signals
        while not self._stop_event.is_set():
            try:
                pending = await tools.fetch_pending_signals(self._pool)
                for signal in pending:
                    broker = self._broker_factory.get_broker()
                    await self._risk.process_signal(
                        self._pool, signal["id"], broker,
                        strategy_pool=self._strategy_pool,
                    )
            except Exception:
                logger.error("risk_poll_error", exc_info=True)

            if await self._wait_or_stop(RISK_POLL_INTERVAL):
                break

    async def _executor_poll_loop(self) -> None:
        # / poll for pending approved trades
        while not self._stop_event.is_set():
            try:
                pending = await tools.fetch_pending_trades(self._pool)
                for trade in pending:
                    broker = self._broker_factory.get_broker()
                    await self._executor.execute_trade(
                        self._pool, trade["id"], broker,
                    )
            except Exception:
                logger.error("executor_poll_error", exc_info=True)

            if await self._wait_or_stop(EXECUTOR_POLL_INTERVAL):
                break

    async def _evolution_loop(self) -> None:
        # / run evolution engine at midnight et
        while not self._stop_event.is_set():
            # / calculate seconds until midnight et
            et = timezone(timedelta(hours=-5))
            now = datetime.now(et)
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            wait_seconds = (midnight - now).total_seconds()

            logger.info("evolution_waiting", next_run=str(midnight), wait_seconds=wait_seconds)

            if await self._wait_or_stop(wait_seconds):
                break

            try:
                await self._evolution.run(self._pool, self._strategy_pool)
            except Exception as exc:
                logger.error("evolution_loop_error", exc_info=True)
                notify_system_error(str(exc), "evolution_loop")

    async def _insider_backfill_loop(self) -> None:
        # / refresh insider trades from sec edgar daily at 6am et
        while not self._stop_event.is_set():
            et = timezone(timedelta(hours=-5))
            now = datetime.now(et)
            target = now.replace(hour=6, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)

            logger.info("insider_backfill_waiting", next_run=str(target))

            if await self._wait_or_stop((target - now).total_seconds()):
                break

            try:
                from src.data.sec_filings import fetch_insider_trades, store_insider_trades
                from src.data.symbols import get_sector
                symbols = [s for s in self._get_symbols() if not is_crypto(s) and get_sector(s) != "etfs"]
                for symbol in symbols:
                    try:
                        trades = await fetch_insider_trades(symbol)
                        if trades:
                            await store_insider_trades(self._pool, trades)
                    except Exception as exc:
                        logger.warning("insider_backfill_symbol_error", symbol=symbol, error=str(exc))
            except Exception as exc:
                logger.error("insider_backfill_error", exc_info=True)
                notify_system_error(str(exc), "insider_backfill")

    async def _fundamentals_backfill_loop(self) -> None:
        # / refresh fundamentals from edgar/finnhub/yfinance daily at 7am et
        while not self._stop_event.is_set():
            et = timezone(timedelta(hours=-5))
            now = datetime.now(et)
            target = now.replace(hour=7, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)

            logger.info("fundamentals_backfill_waiting", next_run=str(target))

            if await self._wait_or_stop((target - now).total_seconds()):
                break

            try:
                from src.data.fundamentals import fetch_all_fundamentals, store_fundamentals
                symbols = [s for s in self._get_symbols() if not is_crypto(s)]
                data = await fetch_all_fundamentals(symbols)
                if data:
                    await store_fundamentals(self._pool, data)
                    logger.info("fundamentals_backfill_complete", count=len(data))
            except Exception as exc:
                logger.error("fundamentals_backfill_error", exc_info=True)
                notify_system_error(str(exc), "fundamentals_backfill")

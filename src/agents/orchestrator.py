# / agent orchestrator — coordinates all trading agents on schedule
# / runs analyst, strategy, risk, executor, and evolution loops concurrently
# / uses exchange_calendars for nyse market hours detection

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import structlog
import pandas as pd

from src.agents import tools
from src.agents import loop_registry
from src.agents.alert_engine import check_and_fire as alert_check_and_fire
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
ANALYST_MARKET_HOURS = 3600      # / 60 minutes (data refreshes every 2h)
ANALYST_OFF_HOURS = 3600         # / 60 minutes
STRATEGY_MARKET_HOURS = 300      # / 5 minutes
STRATEGY_OFF_HOURS = 300         # / 5 minutes (consistent for crypto)
DEEPSEEK_INTERVAL = 3600         # / 1 hour
INTRADAY_INTERVAL = 3600         # / 1 hour
RISK_POLL_INTERVAL = 5           # / 5 seconds
EXECUTOR_POLL_INTERVAL = 5       # / 5 seconds
STRATEGY_METRICS_INTERVAL = 3600 # / 1 hour
ALTERNATIVE_DATA_INTERVAL = 86400  # / 24 hours
MONITORING_INTERVAL = 3600         # / 1 hour
COST_FLUSH_INTERVAL = 3600         # / 1 hour
DAILY_BAR_INTERVAL = 14400         # / 4 hours
PRICE_REFRESH_INTERVAL = 300       # / 5 minutes
ALERT_CHECK_INTERVAL = 30          # / 30 seconds — isolated from strategy cycles
CRYPTO_BACKFILL_INTERVAL = 1800    # / 30 minutes — crypto is 24/7, no ET gate
REGIME_LOOP_INTERVAL = 21600       # / 6 hours — regime history refresh
KNOWLEDGE_HYDRATION_INTERVAL = 86400  # / 24 hours — daily wiki enrichment pass
KNOWLEDGE_HYDRATION_STARTUP_DELAY = 900  # / 15 min offset so we don't stack LLM calls at startup
KNOWLEDGE_HYDRATION_DEFAULT_CAP = 5  # / hard cap on symbols enriched per day (free-tier LLM budget)
WIKI_STUB_WORD_THRESHOLD = 150    # / docs below this word count count as seed stubs
WIKI_MIN_ANALYSIS_ROWS = 5        # / need N recent analyses before we have enough signal to enrich


class AgentOrchestrator:
    def __init__(self, mode: str = "paper"):
        self._mode = mode
        self._stop_event: asyncio.Event = asyncio.Event()
        self._pool = None
        self._broker_factory: BrokerFactory | None = None
        self._strategy_pool = StrategyPool()
        # / load risk_limits once and pass to agents that enforce them
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
        # / phase 2: track last known regime per market to fire on_regime_shift on transitions
        self._last_equity_regime: str | None = None
        self._last_crypto_regime: str | None = None

    @staticmethod
    def _load_risk_limits() -> dict:
        # / load configs/risk_limits.json once at startup; shared by all agents
        from pathlib import Path
        path = Path(__file__).parent.parent.parent / "configs" / "risk_limits.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception as exc:
                logger.warning("risk_limits_load_failed", error=str(exc)[:120])
        return {}

    async def start(self) -> None:
        # / initialize resources and start all agent loops
        logger.info("orchestrator_starting", mode=self._mode)

        # / init db
        self._pool = await init_db()

        # / prune old system events (keep 30 days). table may not exist on first run.
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM system_events WHERE timestamp < NOW() - INTERVAL '30 days'"
                )
        except Exception as exc:
            # / explicitly tolerate UndefinedTable on bootstrap; log anything else
            msg = str(exc).lower()
            if "does not exist" not in msg and "undefined" not in msg:
                logger.warning("system_events_prune_failed", error=str(exc)[:120])

        # / sync trade_log from alpaca (source of truth) and clean stale PaperBroker data
        try:
            # / remove ghost trades from in-memory PaperBroker (order_id is a uuid, alpaca uses different format)
            async with self._pool.acquire() as conn:
                cleaned = await conn.execute(
                    """DELETE FROM trade_log WHERE broker = 'PaperBroker'
                    OR (broker IS NULL AND order_id ~ '^[0-9a-f]{8}-')"""
                )
                if cleaned != "DELETE 0":
                    logger.info("cleaned_stale_paper_trades", result=cleaned)
            synced = await tools.sync_trades_from_alpaca(self._pool)
            if synced:
                logger.info("startup_alpaca_sync", trades_synced=synced)
            # / bootstrap strategy positions from alpaca for pre-existing holdings
            pos_synced = await tools.sync_strategy_positions_from_alpaca(self._pool)
            if pos_synced:
                logger.info("startup_position_sync", positions_synced=pos_synced)
            # / bug e one-shot: backfill historical trade_log pnl for sells with null pnl
            backfilled = await tools.backfill_trade_pnl(self._pool)
            if backfilled:
                logger.info("startup_pnl_backfill", updated=backfilled)
        except Exception:
            logger.debug("startup_sync_failed", exc_info=True)

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
            # / phase 2: knowledge base upkeep
            asyncio.create_task(self._wiki_embedding_loop(), name="wiki_embedding"),
            asyncio.create_task(self._wiki_archive_loop(), name="wiki_archive"),
            # / phase 5 step 3: daily symbol wiki hydration (writes stubs back as playbooks)
            asyncio.create_task(self._knowledge_hydration_loop(), name="knowledge_hydration"),
            # / phase 6 step 1: pull trigger_requests rows posted by dashboard /api/admin/trigger
            asyncio.create_task(self._trigger_poll_loop(), name="trigger_poll"),
            # / phase 6 step 10: weekly kelly-weighted capital allocation refresh
            asyncio.create_task(self._capital_allocator_loop(), name="capital_allocator"),
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

        # / close shared http clients (best-effort, may already be torn down)
        try:
            from src.data.resilience import close_http_client
            from src.data.llm_client import close_llm_clients
            from src.data.alpaca_client import close_alpaca_client
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
        # / get symbols to analyze from environment or default
        symbols_env = os.environ.get("TRADE_SYMBOLS")
        if symbols_env:
            return [s.strip() for s in symbols_env.split(",") if s.strip()]
        return FULL_UNIVERSE

    @staticmethod
    def _et_tz():
        # / dst-aware eastern time, fallback to fixed est
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo("America/New_York")
        except Exception:
            return timezone(timedelta(hours=-5))

    async def _sleep_until_et_hour(self, hour: int):
        # / wait until target hour in eastern time (dst-aware)
        et = self._et_tz()
        now = datetime.now(et)
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait = (target - now).total_seconds()
        return await self._wait_or_stop(wait), target

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
            now = datetime.now(self._et_tz())
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
                async with loop_registry.track(self._pool, "analyst"):
                    symbols = self._get_symbols()
                    await self._analyst.run(self._pool, symbols, run_deepseek=False)
                    # / broadcast analysis update (fire-and-forget); dashboard may not be running
                    try:
                        from src.dashboard.app import broadcast, _ws_clients
                        if _ws_clients:
                            asyncio.create_task(broadcast("analysis_update", {"cycle": "complete"}))
                    except Exception as exc:
                        logger.debug("broadcast_analysis_failed", error=str(exc)[:100])
            except Exception as exc:
                logger.error("analyst_loop_error", exc_info=True)
                notify_system_error(str(exc), "analyst_loop")

            if await self._wait_or_stop(interval):
                break

    async def _deepseek_loop(self) -> None:
        # / run deepseek analysis hourly (separate from groq every-cycle)
        # / first run after short delay to let initial groq cycle start
        first_run = True
        while not self._stop_event.is_set():
            wait = 120 if first_run else DEEPSEEK_INTERVAL
            first_run = False
            if await self._wait_or_stop(wait):
                break
            try:
                async with loop_registry.track(self._pool, "deepseek"):
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
            stopped, target = await self._sleep_until_et_hour(17)

            logger.info("reasoner_waiting", next_run=str(target))

            if stopped:
                break

            try:
                async with loop_registry.track(self._pool, "reasoner"):
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
                async with loop_registry.track(self._pool, "strategy"):
                    broker = self._broker_factory.get_broker()
                    await self._strategy.run(
                        self._pool, self._strategy_pool, broker,
                    )
                    # / broadcast strategy evaluation (fire-and-forget); dashboard may not be running
                    try:
                        from src.dashboard.app import broadcast, _ws_clients
                        if _ws_clients:
                            asyncio.create_task(broadcast("strategy_update", {"cycle": "complete"}))
                    except Exception as exc:
                        logger.debug("broadcast_strategy_failed", error=str(exc)[:100])
            except Exception as exc:
                logger.error("strategy_loop_error", exc_info=True)
                notify_system_error(str(exc), "strategy_loop")

            if await self._wait_or_stop(interval):
                break

    async def _risk_poll_loop(self) -> None:
        # / poll for pending trade signals, process each independently
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "risk"):
                    pending = await tools.fetch_pending_signals(self._pool)
                    for signal in pending:
                        try:
                            broker = self._broker_factory.get_broker()
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
                            # / mark signal as error to prevent infinite retry
                            logger.error("risk_signal_error", signal_id=signal["id"], error=str(exc))
                            try:
                                await tools.update_trade_status(
                                    self._pool, "trade_signals", signal["id"], "error",
                                )
                            except Exception as inner:
                                # / db write failure: signal stuck pending, will retry next poll
                                logger.warning(
                                    "risk_poll_status_update_failed",
                                    signal_id=signal["id"], error=str(inner)[:120],
                                )
            except Exception:
                logger.error("risk_poll_error", exc_info=True)

            if await self._wait_or_stop(RISK_POLL_INTERVAL):
                break

    async def _executor_poll_loop(self) -> None:
        # / poll for pending approved trades
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "executor"):
                    pending = await tools.fetch_pending_trades(self._pool)
                    for trade in pending:
                        broker = self._broker_factory.get_broker()
                        await self._executor.execute_trade(
                            self._pool, trade["id"], broker, strategy_pool=self._strategy_pool,
                        )
            except Exception:
                logger.error("executor_poll_error", exc_info=True)

            if await self._wait_or_stop(EXECUTOR_POLL_INTERVAL):
                break

    async def _evolution_loop(self) -> None:
        # / run evolution engine at midnight et
        while not self._stop_event.is_set():
            stopped, target = await self._sleep_until_et_hour(0)

            logger.info("evolution_waiting", next_run=str(target))

            if stopped:
                break

            try:
                async with loop_registry.track(self._pool, "evolution"):
                    # / gate: evolve if pool has 3+ strategies loaded
                    if self._strategy_pool.size < 3:
                        logger.info("evolution_skipped_small_pool", pool_size=self._strategy_pool.size)
                    else:
                        # / fetch market data for backtesting mutations
                        market_data = await self._fetch_evolution_market_data()
                        # / phase 2: pass current equity regime so wiki context loads regime-matched notes
                        current_regime = await tools.fetch_latest_regime(self._pool, "equity")
                        await self._evolution.run(
                            self._pool, self._strategy_pool,
                            market_data=market_data, regime=current_regime,
                        )
            except Exception as exc:
                logger.error("evolution_loop_error", exc_info=True)
                notify_system_error(str(exc), "evolution_loop")

    async def _insider_backfill_loop(self) -> None:
        # / refresh insider trades from sec edgar daily at 6am et
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
                    await tools.log_event(
                        self._pool, "info", "insider_backfill",
                        f"symbols={len(symbols)} trades={total_trades}",
                    )
            except Exception as exc:
                logger.error("insider_backfill_error", exc_info=True)
                notify_system_error(str(exc), "insider_backfill")
                await tools.log_event(self._pool, "error", "insider_backfill", str(exc)[:200])

    async def _fundamentals_backfill_loop(self) -> None:
        # / refresh fundamentals from edgar/finnhub/yfinance daily at 7am et
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
                    await tools.log_event(
                        self._pool, "info", "fundamentals_backfill",
                        f"symbols={len(symbols)} updated={len(data) if data else 0}",
                    )
            except Exception as exc:
                logger.error("fundamentals_backfill_error", exc_info=True)
                notify_system_error(str(exc), "fundamentals_backfill")
                await tools.log_event(self._pool, "error", "fundamentals_backfill", str(exc)[:200])

    async def _crypto_backfill_loop(self) -> None:
        # / refresh crypto ohlcv bars every 30 min (24/7, no ET gate)
        # / also logs market-cap/volume metadata via coingecko as a side-channel
        if await self._wait_or_stop(120):
            return
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "crypto_backfill"):
                    from src.data.market_data import backfill
                    from src.data.crypto_data import fetch_coin_data
                    crypto_symbols = [s for s in self._get_symbols() if is_crypto(s)]
                    if crypto_symbols:
                        # / alpaca v1beta3/crypto path writes real ohlcv into market_data
                        results = await backfill(self._pool, crypto_symbols, years=1)
                        total = sum(results.values())
                        if total:
                            logger.info("crypto_bar_backfill_complete", symbols=len(crypto_symbols), bars=total)
                        # / side-channel: coingecko metadata (market cap, total volume)
                        for symbol in crypto_symbols:
                            try:
                                data = await fetch_coin_data(symbol)
                                if data and self._pool:
                                    await tools.log_event(
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
        # / compute equity + crypto regime history every 6h (not gated on market hours)
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
                    # / phase 5 step 4: per-sector regimes so strategies can see sector-specific nuance
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
                    await tools.log_event(
                        self._pool, "info", "regime_backfill",
                        f"equity={equity_count} crypto={crypto_count} sectors={sector_total}",
                    )
                    # / phase 2: detect regime transitions and write wiki note + regime_shifts row
                    await self._check_regime_shift("equity")
                    await self._check_regime_shift("crypto")
                    # / phase 5 step 3: write daily snapshot rows so the timeline widget has
                    # / data points on non-shift days (otherwise it only populates on transitions)
                    try:
                        equity_regime = await tools.fetch_latest_regime(self._pool, "equity")
                        crypto_regime = await tools.fetch_latest_regime(self._pool, "crypto")
                        if equity_regime:
                            await snapshot_regime_daily(self._pool, "equity", equity_regime)
                        if crypto_regime:
                            await snapshot_regime_daily(self._pool, "crypto", crypto_regime)
                    except Exception as exc:
                        logger.warning("regime_snapshot_failed", error=str(exc)[:200])
            except Exception as exc:
                logger.error("regime_loop_error", exc_info=True)
                notify_system_error(str(exc), "regime_loop")
                await tools.log_event(self._pool, "error", "regime_backfill", str(exc)[:200])

            if await self._wait_or_stop(REGIME_LOOP_INTERVAL):
                break

    async def _check_regime_shift(self, market: str) -> None:
        # / compare latest regime against last-known; on change, call on_regime_shift
        try:
            latest = await tools.fetch_latest_regime(self._pool, market)
        except Exception:
            return
        if latest is None:
            return
        attr = "_last_equity_regime" if market == "equity" else "_last_crypto_regime"
        previous = getattr(self, attr)
        if previous is None:
            # / first observation — seed without firing
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
        # / fetch 1h intraday bars for all symbols, then aggregate 1h into 2h bars
        # / bug e: 2h timeframe previously had zero rows; aggregate after 1h backfill
        # / also emits cycle_ok events so /api/health surfaces this loop
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "intraday_backfill"):
                    from src.data.market_data import backfill_intraday, aggregate_intraday_to_2h
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
                    await tools.log_event(
                        self._pool, "info", "intraday_backfill",
                        f"1h={total_1h} 2h={total_2h} empty={len(missing)}",
                    )
            except Exception as exc:
                logger.error("intraday_backfill_error", exc_info=True)
                notify_system_error(str(exc), "intraday_backfill")
                await tools.log_event(self._pool, "error", "intraday_backfill", str(exc)[:200])

            if await self._wait_or_stop(INTRADAY_INTERVAL):
                break

    async def _daily_bar_backfill_loop(self) -> None:
        # / refresh daily ohlcv bars for all symbols every 4h
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
                    await tools.log_event(
                        self._pool, "info", "daily_bar_backfill", f"symbols={len(symbols)} bars={total}",
                    )
            except Exception as exc:
                logger.error("daily_bar_backfill_error", exc_info=True)
                notify_system_error(str(exc), "daily_bar_backfill")
                await tools.log_event(self._pool, "error", "daily_bar_backfill", str(exc)[:200])

            if await self._wait_or_stop(DAILY_BAR_INTERVAL):
                break

    async def _price_refresh_loop(self) -> None:
        # / refresh current prices via yfinance during market hours
        # / writes to latest_prices table (one row per symbol) — NOT market_data_intraday
        # / the old pattern polluted intraday with zero-volume snapshots and broke candles
        if await self._wait_or_stop(60):
            return
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "price_refresh"):
                    if self._is_market_hours():
                        from src.data.market_data import fetch_latest_prices, store_latest_prices
                        symbols = self._get_symbols()
                        prices = await fetch_latest_prices(symbols)
                        if prices:
                            stored = await store_latest_prices(self._pool, prices)
                            logger.info("price_refresh_complete", symbols=stored)
                            await tools.log_event(
                                self._pool, "info", "price_refresh", f"symbols={stored}",
                            )
                        else:
                            logger.warning("price_refresh_empty")
                            await tools.log_event(self._pool, "warning", "price_refresh", "no_prices_fetched")
            except Exception as exc:
                logger.warning("price_refresh_error", error=str(exc)[:100])
                notify_system_error(f"price refresh failed: {str(exc)[:80]}", "price_refresh")
                await tools.log_event(self._pool, "error", "price_refresh", str(exc)[:200])

            if await self._wait_or_stop(PRICE_REFRESH_INTERVAL):
                break

    async def _alpaca_sync_loop(self) -> None:
        # / periodically sync filled orders from alpaca into trade_log + reconcile positions
        while not self._stop_event.is_set():
            async with loop_registry.track(self._pool, "alpaca_sync"):
                try:
                    synced = await tools.sync_trades_from_alpaca(self._pool)
                    if synced:
                        logger.info("alpaca_periodic_sync", trades_synced=synced)
                except Exception:
                    logger.debug("alpaca_sync_error", exc_info=True)

                await self._alpaca_reconcile_positions()

            # / sync every 5 minutes
            if await self._wait_or_stop(300):
                break

    async def _alpaca_reconcile_positions(self) -> None:
        # / reconcile strategy_positions vs alpaca positions
        try:
            # / aggregate tracked qty per symbol from db
            all_positions = await tools.get_strategy_positions(self._pool)
            tracked: dict[str, float] = {}
            for p in all_positions:
                tracked[p["symbol"]] = tracked.get(p["symbol"], 0) + p["qty"]

            # / get alpaca positions (source of truth)
            broker = self._broker_factory.get_broker()
            alpaca_positions = await broker.get_positions()
            alpaca_map: dict[str, float] = {p.symbol: p.qty for p in alpaca_positions}
            # / bug e: carry avg_entry_price for untracked projection so cost basis isn't zero
            alpaca_prices: dict[str, float] = {
                p.symbol: float(p.avg_entry_price or 0) for p in alpaca_positions
            }

            drift_found = False
            current_drift: dict[str, float] = {}

            # / check each alpaca position against tracked
            for symbol, alpaca_qty in alpaca_map.items():
                tracked_qty = tracked.pop(symbol, 0)
                if abs(tracked_qty - alpaca_qty) > 0.0001:
                    current_drift[symbol] = alpaca_qty
                    logger.warning("position_drift", symbol=symbol, tracked=tracked_qty, alpaca=alpaca_qty)
                    # / only alert on new drift, not every cycle
                    if symbol not in self._last_drift:
                        notify_system_error(f"position drift: {symbol} tracked={tracked_qty} alpaca={alpaca_qty}", "reconciliation")
                    drift_found = True

            # / check tracked symbols no longer in alpaca (sold externally)
            for symbol, tracked_qty in tracked.items():
                if tracked_qty > 0.0001:
                    current_drift[symbol] = 0
                    logger.warning("position_drift", symbol=symbol, tracked=tracked_qty, alpaca=0)
                    if symbol not in self._last_drift:
                        notify_system_error(f"position closed externally: {symbol} (was {tracked_qty})", "reconciliation")
                    drift_found = True

            self._last_drift = current_drift

            # / auto-fix: update drifted positions without destroying attribution
            if drift_found:
                # / bug c: full_sync=true bypasses empty-alpaca guard after confirmed drift
                await tools.reconcile_strategy_positions(
                    self._pool, alpaca_map, full_sync=True, price_map=alpaca_prices,
                )
                logger.info("position_reconciliation_auto_fixed")
            else:
                logger.debug("position_reconciliation_ok", symbols=len(alpaca_map))
        except Exception:
            logger.warning("position_reconciliation_error", exc_info=True)

    async def _macro_backfill_loop(self) -> None:
        # / refresh fred macro data daily at 9am et
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
                    await tools.log_event(
                        self._pool, "info", "macro_backfill",
                        f"indicators={len(indicators) if indicators else 0}",
                    )
            except Exception as exc:
                logger.error("macro_backfill_error", exc_info=True)
                notify_system_error(str(exc), "macro_backfill")
                await tools.log_event(self._pool, "error", "macro_backfill", str(exc)[:200])

    async def _alert_loop(self) -> None:
        # / isolated price-cross alert scanner — never shares state with strategy/risk agents
        # / runs every ALERT_CHECK_INTERVAL seconds, batches discord fires, ws-broadcasts each hit
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "alert"):
                    broker = self._broker_factory.get_broker() if self._broker_factory else None
                    if broker is not None:
                        # / late-bind ws broadcast so tests that don't mount the dashboard still pass
                        ws_broadcast = None
                        try:
                            from src.dashboard.app import broadcast as ws_broadcast_fn, _ws_clients
                            if _ws_clients:
                                ws_broadcast = ws_broadcast_fn
                        except Exception:
                            ws_broadcast = None
                        await alert_check_and_fire(
                            self._pool, broker, ws_broadcast, webhook_url,
                            self._alert_prev_prices,
                        )
                        await tools.log_event(self._pool, "info", "alert", "cycle_ok")
            except Exception as exc:
                logger.error("alert_loop_error", exc_info=True)
                notify_system_error(str(exc), "alert_loop")
                await tools.log_event(self._pool, "error", "alert", str(exc)[:200])

            if await self._wait_or_stop(ALERT_CHECK_INTERVAL):
                break

    async def _alternative_data_loop(self, use_registry: bool = True) -> None:
        # / backfill alternative data: analyst ratings, short interest, options, etc
        # / use_registry=True (default): drive the cycle from src.data.source_registry.
        # / use_registry=False: legacy per-source code path (kept as fallback for the one
        # / cycle of transition; remove once registry has proven itself in prod)
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
                await tools.log_event(self._pool, "error", "alternative_data", str(exc)[:200])
            if await self._wait_or_stop(ALTERNATIVE_DATA_INTERVAL):
                break

    async def _run_alternative_data_registry_cycle(self) -> None:
        # / canonical path — iterate registered alt-data sources
        from src.data.source_registry import all_sources
        from src.data.symbols import get_sector

        sources = all_sources()
        # / one fetch per (source.name) per symbol — analyst_ratings is registered twice
        # / (for two fields) but we only want to hit yfinance once per symbol
        by_name_deduped: dict[str, "AltDataSource"] = {}  # noqa: F821
        for src in sources:
            by_name_deduped.setdefault(src.name, src)

        # / global sources first (macro) — one fetch, no symbol iteration
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
                    # / dark_pool accepts (symbol, pool=...) — detect via kwarg name
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
        await tools.log_event(
            self._pool, "info", "alternative_data", f"symbols={len(symbols)} via=registry",
        )

    async def _run_alternative_data_legacy_cycle(self) -> None:
        # / legacy per-source hardcoded code path — kept as fallback during the transition
        # / deprecated path
        from src.data.analyst_ratings import fetch_analyst_ratings, store_analyst_ratings
        from src.data.earnings_revisions import fetch_earnings_estimates, store_earnings_estimates
        from src.data.short_interest import fetch_short_interest, store_short_interest
        from src.data.dark_pool import fetch_dark_pool_data, store_dark_pool
        from src.data.options_data import fetch_options_data, store_options_data
        from src.data.congressional_trades import fetch_congressional_trades, store_congressional_trades
        from src.data.symbols import get_sector

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
                # / short interest (works for etfs too)
                si = await fetch_short_interest(symbol)
                if si:
                    await store_short_interest(self._pool, si)
                # / dark pool — pass pool so ratio is computed + stored in one call
                dp = await fetch_dark_pool_data(symbol, pool=self._pool)
                if dp:
                    await store_dark_pool(self._pool, dp)
            except Exception as exc:
                logger.warning("alt_data_symbol_error", symbol=symbol, error=str(exc))
            await asyncio.sleep(2)  # / throttle api calls
        logger.info("alternative_data_backfill_complete", count=len(symbols), via="legacy")
        await tools.log_event(
            self._pool, "info", "alternative_data", f"symbols={len(symbols)} via=legacy",
        )

    async def _monitoring_loop(self) -> None:
        # / run monitoring checks: staleness, strategy decay, correlation
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
                    broker = self._broker_factory.get_broker()
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
        # / flush llm cost tracker to db hourly
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
        # / load daily ohlcv from db for all symbols, used by evolution backtesting
        symbols = self._get_symbols()
        market_data: dict[str, pd.DataFrame] = {}
        async with self._pool.acquire() as conn:
            for symbol in symbols:
                try:
                    rows = await conn.fetch(
                        """SELECT date, open, high, low, close, volume
                        FROM market_data WHERE symbol = $1
                        ORDER BY date ASC""",
                        symbol,
                    )
                    if len(rows) < 50:
                        continue
                    df = pd.DataFrame(
                        [{
                            "open": float(r["open"]) if r["open"] else 0,
                            "high": float(r["high"]) if r["high"] else 0,
                            "low": float(r["low"]) if r["low"] else 0,
                            "close": float(r["close"]) if r["close"] else 0,
                            "volume": int(r["volume"]) if r["volume"] else 0,
                        } for r in rows],
                        index=pd.DatetimeIndex([r["date"] for r in rows]),
                    )
                    market_data[symbol] = df
                except Exception as exc:
                    logger.warning("evolution_market_data_failed", symbol=symbol, error=str(exc))
        logger.info("evolution_market_data_loaded", symbols=len(market_data))
        return market_data

    async def _strategy_metrics_loop(self) -> None:
        # / compute live strategy metrics from trade_log every hour
        # / short startup delay only; run immediately after so dashboard shows real metrics on first cycle
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
        # / bug a: delegate to richer live_strategy_metrics module
        # / writes rolling sharpe/sortino/maxdd/win rate/composite per strategy × window
        from src.analysis.live_strategy_metrics import compute_live_strategy_metrics
        updated = await compute_live_strategy_metrics(self._pool)
        logger.info("strategy_metrics_computed", strategies=updated)

    # / ---- phase 2: knowledge base loops ----

    async def _wiki_embedding_loop(self) -> None:
        # / backfill missing wiki_embeddings every 6h via ollama nomic-embed-text
        if await self._wait_or_stop(300):
            return
        try:
            async with loop_registry.track(self._pool, "wiki_embedding"):
                from src.knowledge.loops import wiki_embedding_backfill_loop
                await wiki_embedding_backfill_loop(self._pool)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("wiki_embedding_loop_error", error=str(exc)[:200])
            notify_system_error(str(exc), "wiki_embedding_loop")

    async def _wiki_archive_loop(self) -> None:
        # / daily archive of wiki docs older than 180 days
        if await self._wait_or_stop(600):
            return
        try:
            async with loop_registry.track(self._pool, "wiki_archive"):
                from src.knowledge.loops import wiki_archive_loop
                await wiki_archive_loop(self._pool)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("wiki_archive_loop_error", error=str(exc)[:200])
            notify_system_error(str(exc), "wiki_archive_loop")

    # / ---- phase 5 step 3: daily symbol wiki hydration ----

    def _hydration_daily_cap(self) -> int:
        # / configurable via env; defaults to 5 to stay in free-tier llm budget
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
        # / pick up to `cap` symbols whose wiki entry is a seed stub AND have
        # / accumulated enough recent analyses to support a useful rewrite.
        # / round-robin order by wiki_documents.updated_at ASC so stale docs rotate through.
        if cap <= 0 or self._pool is None:
            return []
        symbols = self._get_symbols()
        if not symbols:
            return []
        try:
            async with self._pool.acquire() as conn:
                # / find stub docs first (seed confidence or below word threshold)
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
                # / symbols in the universe without any wiki doc are also stubs — add them last
                have_docs = {r["symbol"] for r in stub_rows}
                for sym in symbols:
                    if sym not in have_docs and sym not in stub_symbols:
                        stub_symbols.append(sym)

                # / filter down to those with >= WIKI_MIN_ANALYSIS_ROWS recent analyses
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

        # / preserve stub ordering (oldest-updated first) when picking the first `cap`
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
        # / fetch last 30 days of analysis_scores + latest fundamentals + last 90 days insider
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
        # / phase 5 step 3: daily enrichment of seed-stub symbol wiki docs
        # / offset from other loops so llm calls don't stack at cold start
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
                        await tools.log_event(
                            self._pool, "info", "knowledge_hydration",
                            f"candidates={len(picks)} enriched={enriched} cap={cap}",
                        )
            except Exception as exc:
                logger.error("knowledge_hydration_loop_error", exc_info=True)
                notify_system_error(str(exc), "knowledge_hydration_loop")

            if await self._wait_or_stop(KNOWLEDGE_HYDRATION_INTERVAL):
                break

    # / ---- phase 6 step 10: weekly kelly-weighted allocator ----

    async def _capital_allocator_loop(self) -> None:
        # / first run after 1h delay so strategy_scores has fresh data; then weekly
        if await self._wait_or_stop(3600):
            return
        while not self._stop_event.is_set():
            try:
                async with loop_registry.track(self._pool, "capital_allocator"):
                    from src.agents.capital_allocator import compute_allocations
                    mpp = float(self._risk_limits.get("max_position_pct", 0.04))
                    allocs = await compute_allocations(self._pool, max_position_pct=mpp)
                    await tools.log_event(
                        self._pool, "info", "capital_allocator",
                        f"strategies={len(allocs)} max_pct={mpp}",
                    )
            except Exception as exc:
                logger.error("capital_allocator_error", exc_info=True)
                notify_system_error(str(exc), "capital_allocator")

            # / refresh weekly
            if await self._wait_or_stop(604800):
                break

    # / ---- phase 6 step 1: dashboard-posted manual triggers ----

    async def _trigger_poll_loop(self) -> None:
        # / poll trigger_requests every 5s and run the matching one-shot work
        # / wait briefly so the db migration has run before we start polling
        if await self._wait_or_stop(10):
            return
        while not self._stop_event.is_set():
            try:
                claimed = await loop_registry.claim_pending_triggers(self._pool)
                for trigger_id, service in claimed:
                    asyncio.create_task(self._run_trigger(trigger_id, service))
            except Exception as exc:
                logger.warning("trigger_poll_error", error=str(exc)[:120])
            if await self._wait_or_stop(5):
                break

    async def _run_trigger(self, trigger_id: int, service: str) -> None:
        # / execute one cycle of the named service in the background
        logger.info("trigger_received", service=service, trigger_id=trigger_id)
        try:
            async with loop_registry.track(self._pool, service):
                await self._run_service_once(service)
            await loop_registry.complete_trigger(self._pool, trigger_id, "done")
        except Exception as exc:
            logger.error("trigger_run_failed", service=service, error=str(exc)[:200])
            await loop_registry.complete_trigger(self._pool, trigger_id, "error", str(exc))

    async def _run_service_once(self, service: str) -> None:
        # / dispatch a service name to its one-shot work (same work as the loop body)
        from src.data.symbols import is_crypto as _is_crypto, get_sector as _get_sector
        symbols = self._get_symbols()
        if service == "macro_backfill":
            from src.data.fred_macro import fetch_macro_indicators
            await fetch_macro_indicators(self._pool)
        elif service == "fundamentals_backfill":
            from src.data.fundamentals import fetch_all_fundamentals, store_fundamentals
            syms = [s for s in symbols if not _is_crypto(s)]
            data = await fetch_all_fundamentals(syms)
            if data:
                await store_fundamentals(self._pool, data)
        elif service == "insider_backfill":
            from src.data.sec_filings import fetch_insider_trades, store_insider_trades
            syms = [s for s in symbols if not _is_crypto(s) and _get_sector(s) != "etfs"]
            for sym in syms:
                try:
                    trades = await fetch_insider_trades(sym)
                    if trades:
                        await store_insider_trades(self._pool, trades)
                except Exception as exc:
                    logger.warning("trigger_insider_symbol_failed", symbol=sym, error=str(exc)[:120])
        elif service == "regime_backfill":
            from src.data.regime_detector import (
                backfill_regimes, backfill_regimes_per_sector, snapshot_regime_daily,
            )
            await backfill_regimes(self._pool, "SPY", "equity")
            await backfill_regimes(self._pool, "BTC-USD", "crypto")
            try:
                await backfill_regimes_per_sector(self._pool)
            except Exception as exc:
                logger.warning("trigger_sector_regime_failed", error=str(exc)[:120])
            try:
                eq = await tools.fetch_latest_regime(self._pool, "equity")
                cr = await tools.fetch_latest_regime(self._pool, "crypto")
                if eq:
                    await snapshot_regime_daily(self._pool, "equity", eq)
                if cr:
                    await snapshot_regime_daily(self._pool, "crypto", cr)
            except Exception as exc:
                logger.warning("trigger_regime_snapshot_failed", error=str(exc)[:120])
        elif service == "daily_bar_backfill":
            from src.data.market_data import backfill
            await backfill(self._pool, symbols, years=1)
        elif service == "intraday_backfill":
            from src.data.market_data import backfill_intraday, aggregate_intraday_to_2h
            await backfill_intraday(self._pool, symbols, days=10, timeframe="1Hour")
            await aggregate_intraday_to_2h(self._pool, symbols, days=10)
        elif service == "crypto_backfill":
            from src.data.market_data import backfill
            crypto = [s for s in symbols if _is_crypto(s)]
            if crypto:
                await backfill(self._pool, crypto, years=1)
        elif service == "price_refresh":
            from src.data.market_data import fetch_latest_prices, store_latest_prices
            prices = await fetch_latest_prices(symbols)
            if prices:
                await store_latest_prices(self._pool, prices)
        elif service == "alternative_data":
            await self._run_alternative_data_registry_cycle()
        elif service == "analyst":
            await self._analyst.run(self._pool, symbols, run_deepseek=False)
        elif service == "deepseek":
            await self._analyst.run(self._pool, symbols, run_deepseek=True)
        elif service == "strategy":
            broker = self._broker_factory.get_broker()
            await self._strategy.run(self._pool, self._strategy_pool, broker)
        elif service == "strategy_metrics":
            await self._compute_strategy_metrics()
        elif service == "wiki_embedding":
            from src.knowledge.loops import _embed_backfill_once
            await _embed_backfill_once(self._pool)
        elif service == "knowledge_hydration":
            cap = self._hydration_daily_cap()
            picks = await self._fetch_hydration_candidates(cap)
            if picks:
                from src.knowledge.wiki_writer import enrich_symbol_doc
                for sym in picks:
                    try:
                        bundle = await self._load_hydration_bundle(sym)
                        await enrich_symbol_doc(self._pool, sym, *bundle)
                    except Exception as exc:
                        logger.warning("trigger_hydration_symbol_failed", symbol=sym, error=str(exc)[:120])
        elif service == "evolution":
            if self._strategy_pool.size >= 3:
                market_data = await self._fetch_evolution_market_data()
                regime = await tools.fetch_latest_regime(self._pool, "equity")
                await self._evolution.run(
                    self._pool, self._strategy_pool,
                    market_data=market_data, regime=regime,
                )
        elif service == "cost_flush":
            from src.data.cost_tracker import flush_to_db
            await flush_to_db(self._pool)
        elif service == "capital_allocator":
            from src.agents.capital_allocator import compute_allocations
            mpp = float(self._risk_limits.get("max_position_pct", 0.04))
            await compute_allocations(self._pool, max_position_pct=mpp)
        else:
            raise ValueError(f"service not triggerable: {service}")

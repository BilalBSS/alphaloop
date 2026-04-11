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
        self._last_drift: dict[str, float] = {}

    async def start(self) -> None:
        # / initialize resources and start all agent loops
        logger.info("orchestrator_starting", mode=self._mode)

        # / init db
        self._pool = await init_db()

        # / prune old system events (keep 30 days)
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM system_events WHERE timestamp < NOW() - INTERVAL '30 days'"
                )
        except Exception:
            pass  # / table may not exist yet on first run

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
        except Exception:
            pass

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
                symbols = self._get_symbols()
                await self._analyst.run(self._pool, symbols, run_deepseek=False)
                # / broadcast analysis update (fire-and-forget)
                try:
                    from src.dashboard.app import broadcast, _ws_clients
                    if _ws_clients:
                        asyncio.create_task(broadcast("analysis_update", {"cycle": "complete"}))
                except Exception:
                    pass  # dashboard may not be running
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
                # / broadcast strategy evaluation (fire-and-forget)
                try:
                    from src.dashboard.app import broadcast, _ws_clients
                    if _ws_clients:
                        asyncio.create_task(broadcast("strategy_update", {"cycle": "complete"}))
                except Exception:
                    pass
            except Exception as exc:
                logger.error("strategy_loop_error", exc_info=True)
                notify_system_error(str(exc), "strategy_loop")

            if await self._wait_or_stop(interval):
                break

    async def _risk_poll_loop(self) -> None:
        # / poll for pending trade signals, process each independently
        while not self._stop_event.is_set():
            try:
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
                        except Exception:
                            pass
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
            stopped, target = await self._sleep_until_et_hour(0)

            logger.info("evolution_waiting", next_run=str(target))

            if stopped:
                break

            try:
                # / gate: evolve if pool has 3+ strategies loaded
                if self._strategy_pool.size < 3:
                    logger.info("evolution_skipped_small_pool", pool_size=self._strategy_pool.size)
                    continue

                # / fetch market data for backtesting mutations
                market_data = await self._fetch_evolution_market_data()
                await self._evolution.run(self._pool, self._strategy_pool, market_data=market_data)
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
            stopped, target = await self._sleep_until_et_hour(7)

            logger.info("fundamentals_backfill_waiting", next_run=str(target))

            if stopped:
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

    async def _crypto_backfill_loop(self) -> None:
        # / refresh crypto market data daily at 8am et
        while not self._stop_event.is_set():
            stopped, target = await self._sleep_until_et_hour(8)

            logger.info("crypto_backfill_waiting", next_run=str(target))

            if stopped:
                break

            try:
                from src.data.crypto_data import fetch_coin_data
                symbols = [s for s in self._get_symbols() if is_crypto(s)]
                for symbol in symbols:
                    try:
                        data = await fetch_coin_data(symbol)
                        if data and self._pool:
                            await tools.log_event(
                                self._pool, "info", "crypto_backfill",
                                f"mcap={data.get('market_cap')}, vol={data.get('total_volume')}",
                                symbol=symbol,
                            )
                    except Exception as exc:
                        logger.warning("crypto_backfill_symbol_error", symbol=symbol, error=str(exc))
                logger.info("crypto_backfill_complete", count=len(symbols))
            except Exception as exc:
                logger.error("crypto_backfill_error", exc_info=True)
                notify_system_error(str(exc), "crypto_backfill")

    async def _intraday_backfill_loop(self) -> None:
        # / fetch 2h intraday bars for all symbols (crypto trades 24/7)
        while not self._stop_event.is_set():
            try:
                from src.data.market_data import backfill_intraday
                symbols = self._get_symbols()
                results = await backfill_intraday(self._pool, symbols, days=10, timeframe="1Hour")
                total = sum(results.values())
                logger.info("intraday_backfill_complete", symbols=len(symbols), bars=total)
            except Exception as exc:
                logger.error("intraday_backfill_error", exc_info=True)
                notify_system_error(str(exc), "intraday_backfill")

            if await self._wait_or_stop(INTRADAY_INTERVAL):
                break

    async def _daily_bar_backfill_loop(self) -> None:
        # / refresh daily ohlcv bars for all symbols every 4h
        if await self._wait_or_stop(120):
            return
        while not self._stop_event.is_set():
            try:
                from src.data.market_data import backfill
                symbols = self._get_symbols()
                results = await backfill(self._pool, symbols, years=1)
                total = sum(results.values())
                if total:
                    logger.info("daily_bar_backfill_complete", symbols=len(symbols), bars=total)
            except Exception as exc:
                logger.error("daily_bar_backfill_error", exc_info=True)
                notify_system_error(str(exc), "daily_bar_backfill")

            if await self._wait_or_stop(DAILY_BAR_INTERVAL):
                break

    async def _price_refresh_loop(self) -> None:
        # / refresh current prices via yfinance during market hours
        if await self._wait_or_stop(60):
            return
        while not self._stop_event.is_set():
            try:
                if self._is_market_hours():
                    from src.data.market_data import fetch_latest_prices
                    symbols = self._get_symbols()
                    prices = await fetch_latest_prices(symbols)
                    if prices:
                        # / store latest prices as intraday bar snapshots
                        from datetime import datetime, timezone
                        now = datetime.now(timezone.utc)
                        async with self._pool.acquire() as conn:
                            for sym, price in prices.items():
                                await conn.execute(
                                    """INSERT INTO market_data_intraday
                                    (symbol, timestamp, timeframe, open, high, low, close, volume)
                                    VALUES ($1, $2, '1Hour', $3, $3, $3, $3, 0)
                                    ON CONFLICT (symbol, timestamp, timeframe) DO UPDATE SET
                                        close = EXCLUDED.close, high = GREATEST(market_data_intraday.high, EXCLUDED.high),
                                        low = LEAST(market_data_intraday.low, EXCLUDED.low)""",
                                    sym, now, price,
                                )
                        logger.info("price_refresh_complete", symbols=len(prices))
                    else:
                        logger.warning("price_refresh_empty")
            except Exception as exc:
                logger.warning("price_refresh_error", error=str(exc)[:100])
                notify_system_error(f"price refresh failed: {str(exc)[:80]}", "price_refresh")

            if await self._wait_or_stop(PRICE_REFRESH_INTERVAL):
                break

    async def _alpaca_sync_loop(self) -> None:
        # / periodically sync filled orders from alpaca into trade_log + reconcile positions
        while not self._stop_event.is_set():
            try:
                synced = await tools.sync_trades_from_alpaca(self._pool)
                if synced:
                    logger.info("alpaca_periodic_sync", trades_synced=synced)
            except Exception:
                logger.debug("alpaca_sync_error", exc_info=True)

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
                    await tools.reconcile_strategy_positions(self._pool, alpaca_map, full_sync=True)
                    logger.info("position_reconciliation_auto_fixed")
                else:
                    logger.debug("position_reconciliation_ok", symbols=len(alpaca_map))
            except Exception:
                logger.warning("position_reconciliation_error", exc_info=True)

            # / sync every 5 minutes
            if await self._wait_or_stop(300):
                break

    async def _macro_backfill_loop(self) -> None:
        # / refresh fred macro data daily at 9am et
        while not self._stop_event.is_set():
            stopped, target = await self._sleep_until_et_hour(9)
            logger.info("macro_backfill_waiting", next_run=str(target))
            if stopped:
                break
            try:
                from src.data.fred_macro import fetch_macro_indicators
                indicators = await fetch_macro_indicators(self._pool)
                if indicators:
                    logger.info("macro_backfill_complete", indicators=len(indicators))
            except Exception as exc:
                logger.error("macro_backfill_error", exc_info=True)
                notify_system_error(str(exc), "macro_backfill")

    async def _alternative_data_loop(self) -> None:
        # / backfill alternative data: analyst ratings, short interest, options, etc
        if await self._wait_or_stop(300):
            return
        while not self._stop_event.is_set():
            try:
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
                        # / dark pool
                        dp = await fetch_dark_pool_data(symbol)
                        if dp:
                            await store_dark_pool(self._pool, dp)
                    except Exception as exc:
                        logger.warning("alt_data_symbol_error", symbol=symbol, error=str(exc))
                    await asyncio.sleep(2)  # / throttle api calls
                logger.info("alternative_data_backfill_complete", count=len(symbols))
            except Exception as exc:
                logger.error("alternative_data_error", exc_info=True)
                notify_system_error(str(exc), "alternative_data")
            if await self._wait_or_stop(ALTERNATIVE_DATA_INTERVAL):
                break

    async def _monitoring_loop(self) -> None:
        # / run monitoring checks: staleness, strategy decay, correlation
        if await self._wait_or_stop(600):
            return
        while not self._stop_event.is_set():
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
        while not self._stop_event.is_set():
            # / initial delay to let other loops populate data
            if await self._wait_or_stop(STRATEGY_METRICS_INTERVAL):
                break
            try:
                await self._compute_strategy_metrics()
            except Exception as exc:
                logger.error("strategy_metrics_loop_error", exc_info=True)
                notify_system_error(str(exc), "strategy_metrics_loop")

    async def _compute_strategy_metrics(self) -> None:
        # / bug a: delegate to richer live_strategy_metrics module
        # / writes rolling sharpe/sortino/maxdd/win rate/composite per strategy × window
        from src.analysis.live_strategy_metrics import compute_live_strategy_metrics
        updated = await compute_live_strategy_metrics(self._pool)
        logger.info("strategy_metrics_computed", strategies=updated)

# / strategy agent — evaluates active strategies against all symbols
# / generates trade signals when entry conditions met
# / uses particle filter to smooth noisy signals

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
import structlog

from src.agents import tools
from src.quant.particle_filter import ParticleFilter
from src.strategies.base_strategy import AnalysisData, ConfigDrivenStrategy
from src.strategies.strategy_pool import StrategyPool

logger = structlog.get_logger(__name__)

# / minimum smoothed strength to generate a signal
SIGNAL_THRESHOLD = 0.3


class StrategyAgent:
    def __init__(self):
        self._filters: dict[str, ParticleFilter] = {}

    async def run(
        self, pool, strategy_pool: StrategyPool, broker,
    ) -> list[dict]:
        # / evaluate all active strategies against all symbols
        # / returns list of generated signal dicts
        signals: list[dict] = []

        # / get active strategies
        active = (
            strategy_pool.list_by_status("paper_trading")
            + strategy_pool.list_by_status("live")
        )
        if not active:
            logger.info("strategy_agent_no_active_strategies")
            return signals

        for entry in active:
            strategy = entry.strategy
            try:
                new_signals = await self._evaluate_strategy(pool, strategy, broker)
                signals.extend(new_signals)
            except Exception as exc:
                logger.warning(
                    "strategy_evaluation_failed",
                    strategy_id=strategy.strategy_id,
                    error=str(exc),
                )

        # / check exits for open positions
        try:
            exit_signals = await self._check_exits(pool, strategy_pool, broker)
            signals.extend(exit_signals)
        except Exception as exc:
            logger.warning("exit_check_failed", error=str(exc))

        logger.info("strategy_agent_complete", signals_generated=len(signals))
        return signals

    async def _evaluate_strategy(
        self, pool, strategy: ConfigDrivenStrategy, broker,
    ) -> list[dict]:
        # / evaluate one strategy against its universe
        signals: list[dict] = []
        universe = strategy.resolve_universe()

        for symbol in universe:
            try:
                signal = await self._evaluate_symbol(pool, strategy, symbol)
                if signal:
                    signals.append(signal)
            except Exception as exc:
                logger.warning(
                    "symbol_evaluation_failed",
                    strategy_id=strategy.strategy_id,
                    symbol=symbol,
                    error=str(exc),
                )

        return signals

    async def _evaluate_symbol(
        self, pool, strategy: ConfigDrivenStrategy, symbol: str,
    ) -> dict | None:
        # / evaluate entry signal for one (strategy, symbol) pair
        # / fetch market data from db
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT date, open, high, low, close, volume
                FROM market_data WHERE symbol = $1
                ORDER BY date DESC LIMIT 250""",
                symbol,
            )

        if len(rows) < 50:
            return None  # / insufficient data

        # / build dataframe (reverse to ascending order)
        rows = list(reversed(rows))
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

        # / fetch analysis data
        analysis_row = await tools.fetch_analysis_score(pool, symbol)
        analysis_data = None
        if analysis_row and analysis_row.get("details"):
            details = analysis_row["details"]
            if isinstance(details, str):
                import json
                details = json.loads(details)
            analysis_data = tools.dict_to_analysis_data(details)

        # / evaluate entry
        entry_signal = strategy.should_enter(symbol, df, analysis_data)

        if not entry_signal.should_enter:
            return None

        # / smooth with particle filter
        smoothed_strength = self._smooth_signal(symbol, entry_signal.strength)
        if smoothed_strength < SIGNAL_THRESHOLD:
            logger.debug(
                "signal_below_threshold",
                symbol=symbol, raw=entry_signal.strength,
                smoothed=smoothed_strength,
            )
            return None

        # / store trade signal
        regime = analysis_row.get("regime") if analysis_row else None
        signal_id = await tools.store_trade_signal(
            pool,
            strategy_id=strategy.strategy_id,
            symbol=symbol,
            signal_type="buy",
            strength=smoothed_strength,
            regime=regime,
            details={
                "raw_strength": entry_signal.strength,
                "smoothed_strength": smoothed_strength,
                "reasons": entry_signal.reasons,
            },
        )

        logger.info(
            "trade_signal_generated",
            strategy_id=strategy.strategy_id,
            symbol=symbol,
            signal_id=signal_id,
            strength=smoothed_strength,
        )
        return {
            "signal_id": signal_id,
            "strategy_id": strategy.strategy_id,
            "symbol": symbol,
            "strength": smoothed_strength,
        }

    def _smooth_signal(self, symbol: str, raw_strength: float) -> float:
        # / use particle filter to smooth noisy entry signals
        if symbol not in self._filters:
            self._filters[symbol] = ParticleFilter(
                n_particles=500, process_noise=0.05, observation_noise=0.3,
            )

        pf = self._filters[symbol]
        pf.predict()
        pf.update(raw_strength)
        return pf.estimate()

    async def _check_exits(
        self, pool, strategy_pool: StrategyPool, broker,
    ) -> list[dict]:
        # / check exit conditions for open positions
        signals: list[dict] = []
        positions = await broker.get_positions()

        for pos in positions:
            # / find which strategy owns this position (simplified: check all active)
            active = (
                strategy_pool.list_by_status("paper_trading")
                + strategy_pool.list_by_status("live")
            )
            for entry in active:
                strategy = entry.strategy
                try:
                    # / fetch market data
                    async with pool.acquire() as conn:
                        rows = await conn.fetch(
                            """SELECT date, open, high, low, close, volume
                            FROM market_data WHERE symbol = $1
                            ORDER BY date DESC LIMIT 250""",
                            pos.symbol,
                        )

                    if len(rows) < 50:
                        continue

                    rows = list(reversed(rows))
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

                    exit_signal = strategy.should_exit(
                        pos.symbol, df, pos.avg_entry_price,
                        pd.Timestamp(df.index[0]), len(df) - 1,
                    )

                    if exit_signal.should_exit:
                        signal_id = await tools.store_trade_signal(
                            pool,
                            strategy_id=strategy.strategy_id,
                            symbol=pos.symbol,
                            signal_type="sell",
                            strength=1.0,
                            regime=None,
                            details={"exit_reason": exit_signal.reason},
                        )
                        signals.append({
                            "signal_id": signal_id,
                            "strategy_id": strategy.strategy_id,
                            "symbol": pos.symbol,
                            "signal_type": "sell",
                        })
                        break  # / one exit signal per position
                except Exception as exc:
                    logger.warning(
                        "exit_check_symbol_failed",
                        symbol=pos.symbol, error=str(exc),
                    )

        return signals

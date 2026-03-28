# / strategy agent — evaluates active strategies against all symbols
# / generates trade signals when entry conditions met
# / uses particle filter to smooth noisy signals

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
import structlog

from src.agents import tools
from src.indicators.momentum import rsi
from src.indicators.trend import sma, macd, adx
from src.indicators.volatility import bollinger_bands, atr
from src.notifications.notifier import notify_strategy_evaluation
from src.quant.particle_filter import ParticleFilter
from src.strategies.base_strategy import AnalysisData, ConfigDrivenStrategy, EntrySignal
from src.strategies.strategy_pool import StrategyPool

logger = structlog.get_logger(__name__)

# / minimum smoothed strength to generate a signal
SIGNAL_THRESHOLD = 0.3


class StrategyAgent:
    def __init__(self):
        self._filters: dict[str, ParticleFilter] = {}
        self._df_cache: dict[str, pd.DataFrame | None] = {}
        self._indicators_stored: set[str] = set()  # / track which symbols had indicators stored this cycle

    async def _fetch_market_df(
        self, pool, symbol: str, min_bars: int = 50,
    ) -> pd.DataFrame | None:
        # / fetch ohlcv and build dataframe, cached per cycle
        if symbol in self._df_cache:
            return self._df_cache[symbol]

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT date, open, high, low, close, volume
                FROM market_data WHERE symbol = $1
                ORDER BY date DESC LIMIT 250""",
                symbol,
            )

        if len(rows) < min_bars:
            self._df_cache[symbol] = None
            return None

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
        self._df_cache[symbol] = df
        return df

    async def run(
        self, pool, strategy_pool: StrategyPool, broker,
    ) -> list[dict]:
        # / evaluate all active strategies against all symbols
        # / returns list of generated signal dicts
        self._df_cache.clear()  # / reset per-cycle cache
        self._indicators_stored.clear()
        signals: list[dict] = []
        stats: dict[str, Any] = {
            "total": 0, "insufficient_data": 0, "no_entry": 0,
            "blocked_consensus": 0, "blocked_threshold": 0,
            "signals": 0, "strategies_evaluated": 0,
            "near_misses": [],
        }

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
            stats["strategies_evaluated"] += 1
            try:
                new_signals = await self._evaluate_strategy(pool, strategy, broker, stats)
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

        # / sort near-misses by strength descending, keep top 3
        stats["near_misses"] = sorted(
            stats["near_misses"], key=lambda nm: nm.get("raw_strength", 0), reverse=True,
        )[:3]

        try:
            notify_strategy_evaluation(stats)
            await tools.store_strategy_evaluation(pool, stats)
        except Exception as exc:
            logger.warning("strategy_eval_observability_failed", error=str(exc))

        logger.info("strategy_agent_complete", signals_generated=len(signals),
                     total_evaluated=stats["total"])
        return signals

    async def _evaluate_strategy(
        self, pool, strategy: ConfigDrivenStrategy, broker,
        stats: dict[str, Any] | None = None,
    ) -> list[dict]:
        # / evaluate one strategy against its universe
        signals: list[dict] = []
        universe = strategy.resolve_universe()

        for symbol in universe:
            try:
                signal = await self._evaluate_symbol(pool, strategy, symbol, stats)
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
        stats: dict[str, Any] | None = None,
    ) -> dict | None:
        # / evaluate entry signal for one (strategy, symbol) pair
        if stats is not None:
            stats["total"] += 1

        df = await self._fetch_market_df(pool, symbol)
        if df is None:
            if stats is not None:
                stats["insufficient_data"] += 1
            return None

        # / compute and store indicators (once per symbol per cycle)
        await self._store_indicators(pool, symbol, df)

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
            if stats is not None:
                stats["no_entry"] += 1
            return None

        # / ai consensus filter: dual-llm agreement gates signal strength
        consensus = analysis_data.ai_consensus if analysis_data else None
        if consensus == "bearish":
            logger.debug("signal_blocked_ai_bearish", symbol=symbol)
            if stats is not None:
                stats["blocked_consensus"] += 1
                stats["near_misses"].append({
                    "symbol": symbol, "raw_strength": entry_signal.strength,
                    "block_reason": "bearish consensus",
                })
            return None
        if consensus == "disagree":
            entry_signal = EntrySignal(
                should_enter=True,
                strength=entry_signal.strength * 0.5,
                reasons=entry_signal.reasons + ["ai_consensus: disagree, halved"],
            )

        # / smooth with particle filter
        smoothed_strength = self._smooth_signal(symbol, entry_signal.strength)
        if smoothed_strength < SIGNAL_THRESHOLD:
            logger.debug(
                "signal_below_threshold",
                symbol=symbol, raw=entry_signal.strength,
                smoothed=smoothed_strength,
            )
            if stats is not None:
                stats["blocked_threshold"] += 1
                stats["near_misses"].append({
                    "symbol": symbol, "raw_strength": entry_signal.strength,
                    "block_reason": f"threshold ({smoothed_strength:.2f} < {SIGNAL_THRESHOLD})",
                })
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

        if stats is not None:
            stats["signals"] += 1

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

    async def _store_indicators(self, pool, symbol: str, df: pd.DataFrame) -> None:
        # / compute and store latest indicator values, once per symbol per cycle
        if symbol in self._indicators_stored or len(df) < 50:
            return
        self._indicators_stored.add(symbol)
        try:
            close = df["close"]
            high, low = df["high"], df["low"]
            rsi_val = rsi(close, 14)
            macd_result = macd(close, 12, 26, 9)
            macd_line, signal_line, hist = macd_result.macd_line, macd_result.signal_line, macd_result.histogram
            adx_val = adx(high, low, close, 14)
            sma20_val = sma(close, 20)
            sma50_val = sma(close, 50)
            bb = bollinger_bands(close, 20, 2.0)
            atr_val = atr(high, low, close, 14)

            indicators = {
                "rsi14": float(rsi_val.iloc[-1]) if not rsi_val.empty else None,
                "macd": float(macd_line.iloc[-1]) if not macd_line.empty else None,
                "macd_signal": float(signal_line.iloc[-1]) if not signal_line.empty else None,
                "macd_histogram": float(hist.iloc[-1]) if not hist.empty else None,
                "adx": float(adx_val.iloc[-1]) if not adx_val.empty else None,
                "sma20": float(sma20_val.iloc[-1]) if not sma20_val.empty else None,
                "sma50": float(sma50_val.iloc[-1]) if not sma50_val.empty else None,
                "bb_upper": float(bb.upper.iloc[-1]) if not bb.upper.empty else None,
                "bb_middle": float(bb.middle.iloc[-1]) if not bb.middle.empty else None,
                "bb_lower": float(bb.lower.iloc[-1]) if not bb.lower.empty else None,
                "atr": float(atr_val.iloc[-1]) if not atr_val.empty else None,
            }
            # / filter NaN
            indicators = {k: (v if v == v else None) for k, v in indicators.items()}
            await tools.store_computed_indicators(pool, symbol, indicators)
        except Exception as exc:
            logger.debug("indicator_compute_failed", symbol=symbol, error=str(exc))

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
                    df = await self._fetch_market_df(pool, pos.symbol)
                    if df is None:
                        continue

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

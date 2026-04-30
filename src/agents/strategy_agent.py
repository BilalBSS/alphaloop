
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import asyncpg
import pandas as pd
import structlog

from src.agents.data_tools import (
    dict_to_analysis_data,
    fetch_analysis_score,
    log_event,
    log_observation,
)
from src.agents.market_tools import (
    fetch_daily_ohlcv,
    fetch_intraday_ohlcv,
    store_computed_indicators,
    store_ict_indicators,
)
from src.agents.position_tools import get_strategy_positions, mark_partial_exit_fired
from src.agents.trade_tools import store_trade_signal
from src.data.strategy_metrics import store_strategy_evaluation
from src.indicators.mean_reversion import hurst_exponent
from src.indicators.momentum import rsi
from src.indicators.structure import fair_value_gaps, order_blocks, structure_breaks
from src.indicators.trend import adx, macd, sma
from src.indicators.volatility import atr, bollinger_bands
from src.notifications.notifier import notify_strategy_evaluation
from src.quant.particle_filter import ParticleFilter
from src.strategies.base_strategy import ConfigDrivenStrategy, EntrySignal
from src.strategies.strategy_pool import StrategyPool

logger = structlog.get_logger(__name__)

SIGNAL_THRESHOLD = 0.10


@dataclass(frozen=True)
class ConsensusResult:
    signal: EntrySignal
    reason_code: str
    kept: bool
    blocked: bool


class StrategyAgent:
    def __init__(self):
        self._filters: dict[str, ParticleFilter] = {}
        self._df_cache: dict[str, pd.DataFrame | None] = {}
        self._intraday_cache: dict[str, pd.DataFrame | None] = {}
        self._indicators_stored: set[str] = set()  # / track which symbols had

    async def _fetch_market_df(
        self, pool, symbol: str, min_bars: int = 50,
    ) -> pd.DataFrame | None:
        if symbol in self._df_cache:
            return self._df_cache[symbol]

        rows = await fetch_daily_ohlcv(pool, symbol, limit=250)

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
        self._df_cache.clear()  # / reset per-cycle cache
        self._intraday_cache.clear()
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

        try:
            exit_signals = await self._check_exits(pool, strategy_pool, broker)
            signals.extend(exit_signals)
        except Exception as exc:
            logger.warning("exit_check_failed", error=str(exc))

        stats["near_misses"] = sorted(
            stats["near_misses"], key=lambda nm: nm.get("raw_strength", 0), reverse=True,
        )[:3]

        try:
            notify_strategy_evaluation(stats)
            await store_strategy_evaluation(pool, stats)
        except Exception as exc:
            logger.warning("strategy_eval_observability_failed", error=str(exc))

        logger.info("strategy_agent_complete", signals_generated=len(signals),
                     total_evaluated=stats["total"])
        entry_hits = stats["total"] - stats["no_entry"] - stats.get("insufficient_data", 0)
        await log_event(
            pool, "info", "strategy",
            f"eval: {stats['total']} pairs, {entry_hits} entry hits, "
            f"{stats.get('blocked_consensus', 0)} consensus blocked, "
            f"{stats.get('blocked_threshold', 0)} threshold blocked",
            details={
                "total": stats["total"], "entry_hits": entry_hits,
                "blocked_consensus": stats.get("blocked_consensus", 0),
                "blocked_threshold": stats.get("blocked_threshold", 0),
                "signals": len(signals),
            },
        )
        return signals

    async def _evaluate_strategy(
        self, pool, strategy: ConfigDrivenStrategy, broker,
        stats: dict[str, Any] | None = None,
    ) -> list[dict]:
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
        if stats is not None:
            stats["total"] += 1

        df = await self._fetch_market_df(pool, symbol)
        if df is None:
            if stats is not None:
                stats["insufficient_data"] += 1
            return None

        await self._store_indicators(pool, symbol, df)

        analysis_data, raw_details, analysis_row = await self._load_analysis(pool, symbol)
        intraday_df = await self._fetch_intraday_df(pool, symbol)
        entry_signal = strategy.should_enter(symbol, df, analysis_data, intraday_df=intraday_df)

        if not entry_signal.should_enter:
            if stats is not None:
                stats["no_entry"] += 1
            await self._log_near_miss(pool, strategy, symbol, entry_signal, analysis_data)
            return None

        # / 2h intraday confirmation gate
        if strategy.config.get("intraday_confirm", True):
            entry_signal = self._apply_intraday_gate(symbol, intraday_df, entry_signal)

        symbol_trend = self._classify_symbol_trend(df)
        regime = analysis_data.regime if analysis_data else None
        bypass_consensus = strategy.get_effective_bypass_consensus(regime)
        consensus = analysis_data.ai_consensus if analysis_data else None
        pre_filter_strength = entry_signal.strength
        result = self._apply_consensus_filter(
            consensus, bypass_consensus, symbol_trend, entry_signal,
        )
        entry_signal = result.signal
        self._log_consensus_decision(
            strategy, symbol, raw_details, consensus, pre_filter_strength,
            result.kept, result.reason_code, symbol_trend, bypass_consensus, regime,
        )
        if result.blocked:
            self._record_blocked_consensus(
                stats, symbol, pre_filter_strength, symbol_trend,
                raw_details, consensus, result.reason_code,
            )
            return None

        threshold = strategy.config.get("signal_threshold_override") or SIGNAL_THRESHOLD
        smoothed_strength = self._smooth_signal(symbol, entry_signal.strength)
        smoothed_strength = await self._apply_ml_modifier(
            pool, symbol, df, analysis_data, smoothed_strength,
        )
        if smoothed_strength < threshold:
            logger.debug(
                "signal_below_threshold",
                symbol=symbol, raw=entry_signal.strength, smoothed=smoothed_strength,
            )
            if stats is not None:
                stats["blocked_threshold"] += 1
                stats["near_misses"].append({
                    "symbol": symbol, "raw_strength": entry_signal.strength,
                    "block_reason": f"threshold ({smoothed_strength:.2f} < {threshold})",
                })
            return None

        # / store + return
        regime = analysis_row.get("regime") if analysis_row else None
        signal_id = await store_trade_signal(
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
            strategy_id=strategy.strategy_id, symbol=symbol,
            signal_id=signal_id, strength=smoothed_strength,
        )
        return {
            "signal_id": signal_id,
            "strategy_id": strategy.strategy_id,
            "symbol": symbol,
            "strength": smoothed_strength,
        }

    async def _load_analysis(self, pool, symbol: str):
        analysis_row = await fetch_analysis_score(pool, symbol)
        analysis_data = None
        raw_details: dict | None = None
        if analysis_row and analysis_row.get("details"):
            details = analysis_row["details"]
            if isinstance(details, str):
                import json
                details = json.loads(details)
            raw_details = details if isinstance(details, dict) else None
            analysis_data = dict_to_analysis_data(details)
        return analysis_data, raw_details, analysis_row

    async def _log_near_miss(
        self, pool, strategy: ConfigDrivenStrategy, symbol: str,
        entry_signal: EntrySignal, analysis_data,
    ) -> None:
        try:
            passed_n = entry_signal.passed_count
            total_n = entry_signal.total_count
            is_fundamental_fail = (
                total_n == 0
                and entry_signal.reasons
                and any(
                    kw in (entry_signal.reasons[0] or "").lower()
                    for kw in ("fundamental", "no fundamental data")
                )
            )
            is_technical_near_miss = total_n > 0 and passed_n == total_n - 1
            if not (is_fundamental_fail or is_technical_near_miss):
                return
            failed_str = "; ".join(entry_signal.failed_reasons or entry_signal.reasons)[:500]
            nm_type = "fundamental_gate" if is_fundamental_fail else "n_minus_1_technical"
            await log_observation(
                pool, strategy.strategy_id, symbol,
                near_miss_type=nm_type,
                passed_count=passed_n, total_count=total_n,
                strength=None, failed_reason=failed_str,
                regime=(analysis_data.regime if analysis_data else None),
            )
        except Exception as exc:
            logger.debug(
                "observation_log_failed",
                strategy_id=strategy.strategy_id, symbol=symbol, error=str(exc)[:100],
            )

    def _apply_intraday_gate(
        self, symbol: str, intraday_df, entry_signal: EntrySignal,
    ) -> EntrySignal:
        if intraday_df is None or len(intraday_df) < 14:
            return entry_signal
        try:
            ic = intraday_df["close"]
            rsi_2h = rsi(ic, 14)
            rsi_val = float(rsi_2h.iloc[-1]) if not rsi_2h.empty else 50.0
            macd_aligned = True
            if len(ic) >= 26:
                m = macd(ic, 12, 26, 9)
                if not m.histogram.empty:
                    macd_aligned = float(m.histogram.iloc[-1]) > 0
            if rsi_val > 75 or not macd_aligned:
                logger.debug(
                    "intraday_gate_halved",
                    symbol=symbol, rsi_2h=rsi_val, macd_aligned=macd_aligned,
                )
                return EntrySignal(
                    should_enter=True,
                    strength=entry_signal.strength * 0.5,
                    reasons=[*entry_signal.reasons,
                        f"2h misaligned (rsi={rsi_val:.0f}, macd_ok={macd_aligned}), halved"],
                )
            if rsi_val < 30 and macd_aligned:
                return EntrySignal(
                    should_enter=True,
                    strength=min(1.0, entry_signal.strength * 1.2),
                    reasons=[*entry_signal.reasons,
                        f"2h confirms entry (rsi={rsi_val:.0f}, macd aligned), boosted 1.2x"],
                )
        except Exception as e:
            logger.debug("intraday_gate_error", symbol=symbol, error=str(e))
        return entry_signal

    def _apply_consensus_filter(
        self, consensus, bypass_consensus: bool, symbol_trend: str,
        entry_signal: EntrySignal,
    ) -> ConsensusResult:
        is_loose = os.environ.get("CONSENSUS_MODE", "strict").strip().lower() == "loose"
        if consensus == "bearish" and not bypass_consensus:
            if symbol_trend == "up":
                return ConsensusResult(
                    signal=EntrySignal(
                        should_enter=True,
                        strength=entry_signal.strength * 0.5,
                        reasons=[*entry_signal.reasons,
                            "ai_consensus: bearish, but symbol uptrend, halved"],
                    ),
                    reason_code="kept_bearish_uptrend_softened", kept=True, blocked=False,
                )
            if is_loose:
                return ConsensusResult(
                    signal=EntrySignal(
                        should_enter=True,
                        strength=entry_signal.strength * 0.4,
                        reasons=[*entry_signal.reasons,
                            "ai_consensus: bearish, loose mode 0.4x"],
                    ),
                    reason_code="kept_bearish_loose_mode", kept=True, blocked=False,
                )
            return ConsensusResult(
                signal=entry_signal, reason_code="rejected_bearish_consensus",
                kept=False, blocked=True,
            )
        if consensus == "bearish" and bypass_consensus:
            return ConsensusResult(
                signal=entry_signal, reason_code="kept_bearish_consensus_bypass",
                kept=True, blocked=False,
            )
        if consensus == "disagree" and not bypass_consensus:
            return ConsensusResult(
                signal=EntrySignal(
                    should_enter=True,
                    strength=entry_signal.strength * 0.7,
                    reasons=[*entry_signal.reasons, "ai_consensus: disagree, reduced 0.7x"],
                ),
                reason_code="kept_disagree_softened", kept=True, blocked=False,
            )
        if consensus == "disagree" and bypass_consensus:
            return ConsensusResult(
                signal=entry_signal, reason_code="kept_disagree_consensus_bypass",
                kept=True, blocked=False,
            )
        if consensus == "bullish":
            return ConsensusResult(
                signal=entry_signal, reason_code="kept_bullish_consensus",
                kept=True, blocked=False,
            )
        if consensus == "neutral":
            return ConsensusResult(
                signal=entry_signal, reason_code="kept_neutral_consensus",
                kept=True, blocked=False,
            )
        return ConsensusResult(
            signal=entry_signal, reason_code="passthrough", kept=True, blocked=False,
        )

    def _log_consensus_decision(
        self, strategy: ConfigDrivenStrategy, symbol: str, raw_details,
        consensus, pre_filter_strength: float, signal_kept: bool,
        reason_code: str, symbol_trend: str, bypass_consensus: bool, regime,
    ) -> None:
        logger.info(
            "consensus_filter_decision",
            strategy_id=strategy.strategy_id, symbol=symbol,
            decision_time=datetime.now(timezone.utc).isoformat(),
            groq_consensus=raw_details.get("llm_signal_groq") if raw_details else None,
            deepseek_consensus=raw_details.get("llm_signal_deepseek") if raw_details else None,
            combined_consensus=consensus,
            raw_signal_strength=pre_filter_strength,
            signal_kept=signal_kept,
            reason_code=reason_code,
            symbol_trend=symbol_trend,
            bypass_consensus=bypass_consensus,
            regime=regime,
        )

    def _record_blocked_consensus(
        self, stats, symbol: str, pre_filter_strength: float, symbol_trend: str,
        raw_details, consensus, reason_code: str,
    ) -> None:
        if stats is None:
            return
        stats["blocked_consensus"] += 1
        stats["near_misses"].append({
            "symbol": symbol,
            "raw_strength": pre_filter_strength,
            "block_reason": f"bearish consensus (trend={symbol_trend})",
            "symbol_trend": symbol_trend,
            "consensus_debug": {
                "groq_consensus": raw_details.get("llm_signal_groq") if raw_details else None,
                "deepseek_consensus": raw_details.get("llm_signal_deepseek") if raw_details else None,
                "combined_consensus": consensus,
                "raw_signal_strength": pre_filter_strength,
                "reason_code": reason_code,
            },
        })

    async def _apply_ml_modifier(
        self, pool, symbol: str, df, analysis_data, smoothed_strength: float,
    ) -> float:
        if len(df) < 252:
            return smoothed_strength
        try:
            from src.quant.ml_signals import train_and_predict
            indicators_dict: dict = {}
            if analysis_data:
                for attr in ("macro_score", "analyst_consensus", "short_pct_float", "iv_rank", "hurst"):
                    val = getattr(analysis_data, attr, None)
                    if val is not None:
                        indicators_dict[attr] = val
            ml_pred = await train_and_predict(df, indicators=indicators_dict or None)
            if not (ml_pred and ml_pred.probability is not None):
                return smoothed_strength
            if ml_pred.probability > 0.6:
                smoothed_strength = min(1.0, smoothed_strength * (1.0 + (ml_pred.probability - 0.5)))
            elif ml_pred.probability < 0.4:
                smoothed_strength = smoothed_strength * (0.5 + ml_pred.probability)
            logger.debug(
                "ml_signal_applied", symbol=symbol,
                ml_prob=ml_pred.probability, adjusted=smoothed_strength,
            )
            try:
                from src.quant.ml_signals import store_ml_prediction
                await store_ml_prediction(pool, ml_pred)
            except Exception as store_exc:
                # / swallow ml store failure
                logger.debug("ml_store_failed", symbol=symbol, error=str(store_exc)[:120])
        except Exception as exc:
            logger.debug("ml_signal_failed", symbol=symbol, error=str(exc))
        return smoothed_strength

    async def _fetch_intraday_df(
        self, pool, symbol: str, min_bars: int = 20,
    ) -> pd.DataFrame | None:
        if symbol in self._intraday_cache:
            return self._intraday_cache[symbol]

        try:
            rows = await fetch_intraday_ohlcv(
                pool, symbol, timeframe="1Hour", limit=100,
            )

            if len(rows) < min_bars:
                self._intraday_cache[symbol] = None
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
                index=pd.DatetimeIndex([r["timestamp"] for r in rows]),
            )
            self._intraday_cache[symbol] = df
            return df
        except (asyncpg.PostgresError, KeyError, ValueError, TypeError):
            self._intraday_cache[symbol] = None
            return None

    async def _store_indicators(self, pool, symbol: str, df: pd.DataFrame) -> None:
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

            # / hurst exponent
            hurst_val = hurst_exponent(close)

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
                "hurst": float(hurst_val) if hurst_val == hurst_val else None,
            }
            # / filter NaN
            indicators = {k: (v if v == v else None) for k, v in indicators.items()}
            await store_computed_indicators(pool, symbol, indicators)

            intraday_df = await self._fetch_intraday_df(pool, symbol)
            if intraday_df is not None and len(intraday_df) >= 20:
                try:
                    ic = intraday_df["close"]
                    ih, il = intraday_df["high"], intraday_df["low"]
                    intraday_ind = {
                        "rsi14": float(rsi(ic, 14).iloc[-1]) if len(ic) >= 14 else None,
                        "macd": None, "macd_signal": None, "macd_histogram": None,
                        "adx": float(adx(ih, il, ic, 14).iloc[-1]) if len(ic) >= 14 else None,
                        "sma20": float(sma(ic, 20).iloc[-1]) if len(ic) >= 20 else None,
                        "sma50": None,
                        "bb_upper": None, "bb_middle": None, "bb_lower": None,
                        "atr": float(atr(ih, il, ic, 14).iloc[-1]) if len(ic) >= 14 else None,
                    }
                    if len(ic) >= 26:
                        m = macd(ic, 12, 26, 9)
                        intraday_ind["macd"] = float(m.macd_line.iloc[-1]) if not m.macd_line.empty else None
                        intraday_ind["macd_signal"] = float(m.signal_line.iloc[-1]) if not m.signal_line.empty else None
                        intraday_ind["macd_histogram"] = float(m.histogram.iloc[-1]) if not m.histogram.empty else None
                    if len(ic) >= 20:
                        bb_2h = bollinger_bands(ic, 20, 2.0)
                        intraday_ind["bb_upper"] = float(bb_2h.upper.iloc[-1]) if not bb_2h.upper.empty else None
                        intraday_ind["bb_middle"] = float(bb_2h.middle.iloc[-1]) if not bb_2h.middle.empty else None
                        intraday_ind["bb_lower"] = float(bb_2h.lower.iloc[-1]) if not bb_2h.lower.empty else None
                    intraday_ind = {k: (v if v == v else None) for k, v in intraday_ind.items()}
                    await store_computed_indicators(pool, symbol, intraday_ind, timeframe="1Hour")
                except Exception as exc2:
                    logger.debug("intraday_indicator_compute_failed", symbol=symbol, error=str(exc2))

            try:
                open_ = df["open"] if "open" in df.columns else close
                fvg_result = fair_value_gaps(high, low, close)
                ob_result = order_blocks(high, low, close, open_)
                sb_result = structure_breaks(high, low, close)

                ict_data = {
                    "fvgs": [
                        {"type": g.type, "high": g.high, "low": g.low, "filled": g.filled}
                        for g in fvg_result.gaps[-10:]
                    ],
                    "order_blocks": [
                        {"type": b.type, "high": b.high, "low": b.low}
                        for b in ob_result.blocks[-8:]
                    ],
                    "structure_breaks": [
                        {"type": s.type, "direction": s.direction, "level": s.level}
                        for s in sb_result.breaks[-8:]
                    ],
                }
                await store_ict_indicators(pool, symbol, ict_data)
            except Exception as exc3:
                logger.debug("ict_indicator_compute_failed", symbol=symbol, error=str(exc3))
        except Exception as exc:
            logger.debug("indicator_compute_failed", symbol=symbol, error=str(exc))

    def _smooth_signal(self, symbol: str, raw_strength: float) -> float:
        if symbol not in self._filters:
            self._filters[symbol] = ParticleFilter(
                n_particles=500, process_noise=0.05, observation_noise=0.10,
            )

        pf = self._filters[symbol]
        pf.predict()
        pf.update(raw_strength)
        return pf.estimate()

    @staticmethod
    def _classify_symbol_trend(df) -> str:
        if df is None or len(df) < 50:
            return "unknown"
        close = df["close"]
        sma50_series = close.rolling(window=50, min_periods=50).mean()
        latest_close = close.iloc[-1]
        latest_sma50 = sma50_series.iloc[-1]
        if pd.isna(latest_sma50):
            return "unknown"
        if float(latest_close) > float(latest_sma50):
            return "up"
        return "down"

    async def _check_exits(
        self, pool, strategy_pool: StrategyPool, broker,
    ) -> list[dict]:
        signals: list[dict] = []
        checked_symbols: set[str] = set()

        active = (
            strategy_pool.list_by_status("paper_trading")
            + strategy_pool.list_by_status("live")
        )

        for entry in active:
            strategy = entry.strategy
            strat_positions = await get_strategy_positions(pool, strategy_id=strategy.strategy_id)
            for sp in strat_positions:
                checked_symbols.add(sp["symbol"])
                exit_sig = await self._eval_exit(pool, strategy, sp)
                if exit_sig:
                    signals.append(exit_sig)

        if active:
            default_strategy = active[0].strategy
            untracked = await get_strategy_positions(pool, strategy_id="untracked")
            for sp in untracked:
                if sp["symbol"] in checked_symbols:
                    continue
                exit_sig = await self._eval_exit(pool, default_strategy, sp, override_strategy_id="untracked")
                if exit_sig:
                    signals.append(exit_sig)

        return signals

    def _eval_partial_exit(
        self, strategy, sp: dict, df: pd.DataFrame,
        override_strategy_id: str | None = None,
    ) -> dict | None:
        # / strategy JSON shape:
        try:
            if sp.get("partial_exit_fired"):
                return None
            cfg = strategy.config if hasattr(strategy, "config") else {}
            tiers = (cfg.get("exit_conditions", {}) or {}).get("partial_exits") or []
            if not tiers:
                return None
            tier = tiers[0]  # / only first tier for
            trigger = (tier.get("trigger") or "").lower()
            threshold = float(tier.get("threshold") or 0)
            fraction = float(tier.get("fraction") or 0.5)
            if not (0.0 < fraction < 1.0) or threshold <= 0:
                return None
            entry_price = float(sp.get("avg_entry_price") or 0)
            last_close = float(df["close"].iloc[-1]) if len(df) > 0 else 0.0
            if entry_price <= 0 or last_close <= 0:
                return None
            gain_pct = (last_close - entry_price) / entry_price
            fires = False
            if trigger in ("take_profit_pct", "take_profit"):
                fires = gain_pct >= threshold
            if not fires:
                return None
            full_qty = float(sp.get("qty") or 0)
            partial_qty = int(full_qty * fraction)
            if partial_qty <= 0:
                return None
            return {
                "strategy_id": override_strategy_id or strategy.strategy_id,
                "qty": partial_qty,
                "fraction": round(fraction, 3),
                "exit_reason": f"partial_exit {trigger} +{threshold*100:.1f}%",
            }
        except Exception as exc:
            logger.debug("partial_exit_eval_failed", symbol=sp.get("symbol"), error=str(exc)[:120])
            return None

    async def _eval_exit(
        self, pool, strategy, sp: dict, override_strategy_id: str | None = None,
    ) -> dict | None:
        try:
            df = await self._fetch_market_df(pool, sp["symbol"])
            if df is None:
                return None

            partial_sig = self._eval_partial_exit(strategy, sp, df, override_strategy_id)
            if partial_sig is not None:
                signal_id = await store_trade_signal(
                    pool,
                    strategy_id=partial_sig["strategy_id"],
                    symbol=sp["symbol"],
                    signal_type="sell",
                    strength=1.0,
                    regime=None,
                    details={
                        "exit_reason": partial_sig["exit_reason"],
                        "qty": partial_sig["qty"],
                        "partial_exit": True,
                        "exit_fraction": partial_sig["fraction"],
                    },
                )
                try:
                    await mark_partial_exit_fired(
                        pool, partial_sig["strategy_id"], sp["symbol"],
                    )
                except Exception as exc:
                    logger.warning(
                        "partial_exit_flag_update_failed",
                        symbol=sp["symbol"], error=str(exc)[:120],
                    )
                return {
                    "signal_id": signal_id,
                    "strategy_id": partial_sig["strategy_id"],
                    "symbol": sp["symbol"],
                    "signal_type": "sell",
                    "qty": partial_sig["qty"],
                }

            entry_ts = sp.get("updated_at")
            entry_date = pd.Timestamp(entry_ts) if entry_ts else pd.Timestamp(df.index[0])
            if entry_date.tz is not None:
                entry_date = entry_date.tz_convert(None)
            exit_signal = strategy.should_exit(
                sp["symbol"], df, sp["avg_entry_price"] or 0,
                entry_date, len(df) - 1,
            )

            if exit_signal.should_exit:
                strat_id = override_strategy_id or strategy.strategy_id
                signal_id = await store_trade_signal(
                    pool,
                    strategy_id=strat_id,
                    symbol=sp["symbol"],
                    signal_type="sell",
                    strength=1.0,
                    regime=None,
                    details={
                        "exit_reason": exit_signal.reason,
                        "qty": sp["qty"],
                    },
                )
                return {
                    "signal_id": signal_id,
                    "strategy_id": strat_id,
                    "symbol": sp["symbol"],
                    "signal_type": "sell",
                    "qty": sp["qty"],
                }
        except Exception as exc:
            logger.warning(
                "exit_check_symbol_failed",
                symbol=sp["symbol"], error=str(exc),
            )
        return None

# / strategy agent — evaluates active strategies against all symbols
# / generates trade signals when entry conditions met
# / uses particle filter to smooth noisy signals

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import structlog

from src.agents import tools
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

# / minimum smoothed strength to generate a signal
SIGNAL_THRESHOLD = 0.15


class StrategyAgent:
    def __init__(self):
        self._filters: dict[str, ParticleFilter] = {}
        self._df_cache: dict[str, pd.DataFrame | None] = {}
        self._intraday_cache: dict[str, pd.DataFrame | None] = {}
        self._indicators_stored: set[str] = set()  # / track which symbols had indicators stored this cycle

    async def _fetch_market_df(
        self, pool, symbol: str, min_bars: int = 50,
    ) -> pd.DataFrame | None:
        # / fetch ohlcv and build dataframe, cached per cycle
        if symbol in self._df_cache:
            return self._df_cache[symbol]

        rows = await tools.fetch_daily_ohlcv(pool, symbol, limit=250)

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
        # / log strategy eval cycle to system_events
        entry_hits = stats["total"] - stats["no_entry"] - stats.get("insufficient_data", 0)
        await tools.log_event(
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
        raw_details: dict | None = None  # / telemetry: keep raw dict for per-llm signals dict_to_analysis_data drops (bug f)
        if analysis_row and analysis_row.get("details"):
            details = analysis_row["details"]
            if isinstance(details, str):
                import json
                details = json.loads(details)
            raw_details = details if isinstance(details, dict) else None
            analysis_data = tools.dict_to_analysis_data(details)

        # / fetch intraday df once for multi-timeframe eval + confirmation gate
        intraday_df = await self._fetch_intraday_df(pool, symbol)

        # / evaluate entry (passes intraday_df for multi-timeframe signals)
        entry_signal = strategy.should_enter(symbol, df, analysis_data, intraday_df=intraday_df)

        if not entry_signal.should_enter:
            if stats is not None:
                stats["no_entry"] += 1
            # / observation_log: surface close-to-firing strategies on the
            # / dashboard without polluting trade_log. record N-1 of N AND
            # / passes (real near-miss) and fundamental-gate failures (the
            # / strict_data silent-drop case). the strategist brief: "add an
            # / observed_only signal tier that logs near-misses ... costs
            # / nothing if you're wrong."
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
                is_technical_near_miss = (
                    total_n > 0 and passed_n == total_n - 1
                )
                if is_fundamental_fail or is_technical_near_miss:
                    failed_str = "; ".join(entry_signal.failed_reasons or entry_signal.reasons)[:500]
                    nm_type = "fundamental_gate" if is_fundamental_fail else "n_minus_1_technical"
                    await tools.log_observation(
                        pool, strategy.strategy_id, symbol,
                        near_miss_type=nm_type,
                        passed_count=passed_n, total_count=total_n,
                        strength=None, failed_reason=failed_str,
                        regime=(analysis_data.regime if analysis_data else None),
                    )
            except Exception as exc:
                logger.debug("observation_log_failed", strategy_id=strategy.strategy_id,
                             symbol=symbol, error=str(exc)[:100])
            return None

        # / 2h intraday confirmation gate
        intraday_confirm = strategy.config.get("intraday_confirm", True)
        if intraday_confirm and intraday_df is not None and len(intraday_df) >= 14:
                try:
                    ic = intraday_df["close"]
                    rsi_2h = rsi(ic, 14)
                    rsi_val = float(rsi_2h.iloc[-1]) if not rsi_2h.empty else 50.0
                    # / check 2h macd alignment if enough bars
                    macd_aligned = True
                    if len(ic) >= 26:
                        m = macd(ic, 12, 26, 9)
                        if not m.histogram.empty:
                            macd_aligned = float(m.histogram.iloc[-1]) > 0
                    # / reject if 2h rsi overbought or macd bearish
                    if rsi_val > 75 or not macd_aligned:
                        entry_signal = EntrySignal(
                            should_enter=True,
                            strength=entry_signal.strength * 0.5,
                            reasons=entry_signal.reasons + [
                                f"2h misaligned (rsi={rsi_val:.0f}, macd_ok={macd_aligned}), halved",
                            ],
                        )
                        logger.debug("intraday_gate_halved", symbol=symbol, rsi_2h=rsi_val, macd_aligned=macd_aligned)
                    elif rsi_val < 30 and macd_aligned:
                        # / 2h confirms oversold + momentum = boost
                        entry_signal = EntrySignal(
                            should_enter=True,
                            strength=min(1.0, entry_signal.strength * 1.2),
                            reasons=entry_signal.reasons + [
                                f"2h confirms entry (rsi={rsi_val:.0f}, macd aligned), boosted 1.2x",
                            ],
                        )
                except Exception as e:
                    logger.debug("intraday_gate_error", symbol=symbol, error=str(e))

        # / classify symbol's own trend from price data
        symbol_trend = self._classify_symbol_trend(df)

        # / ai consensus filter: softened with per-symbol trend overlay
        regime = analysis_data.regime if analysis_data else None
        bypass_consensus = strategy.get_effective_bypass_consensus(regime)
        consensus = analysis_data.ai_consensus if analysis_data else None
        # / telemetry state captured pre-softening (bug f)
        _pre_filter_strength = entry_signal.strength
        _signal_kept = True
        _reason_code = "passthrough"
        _blocked_return = False
        # / phase 6 step 12: consensus mode — strict (default) vs loose.
        # / strict blocks bearish-consensus when symbol_trend != "up".
        # / loose softens bearish to 0.4x but never blocks, giving the evolution
        # / engine more data points to measure brier impact. revert by unset.
        consensus_mode = os.environ.get("CONSENSUS_MODE", "strict").strip().lower()
        is_loose = consensus_mode == "loose"
        if consensus == "bearish" and not bypass_consensus:
            if symbol_trend == "up":
                # / stock bucking the bearish market, allow through with penalty
                entry_signal = EntrySignal(
                    should_enter=True,
                    strength=entry_signal.strength * 0.5,
                    reasons=entry_signal.reasons + [
                        "ai_consensus: bearish, but symbol uptrend, halved",
                    ],
                )
                _reason_code = "kept_bearish_uptrend_softened"
                logger.debug(
                    "signal_softened_bearish_uptrend",
                    symbol=symbol, symbol_trend=symbol_trend,
                    adjusted_strength=entry_signal.strength,
                )
            elif is_loose:
                # / loose mode: never block on bearish consensus, just size way down
                entry_signal = EntrySignal(
                    should_enter=True,
                    strength=entry_signal.strength * 0.4,
                    reasons=entry_signal.reasons + [
                        "ai_consensus: bearish, loose mode 0.4x",
                    ],
                )
                _reason_code = "kept_bearish_loose_mode"
            else:
                # / strict mode: block as before
                _signal_kept = False
                _reason_code = "rejected_bearish_consensus"
                _blocked_return = True
                logger.debug(
                    "signal_blocked_ai_bearish",
                    symbol=symbol, symbol_trend=symbol_trend,
                )
        elif consensus == "bearish" and bypass_consensus:
            _reason_code = "kept_bearish_consensus_bypass"
        elif consensus == "disagree" and not bypass_consensus:
            entry_signal = EntrySignal(
                should_enter=True,
                strength=entry_signal.strength * 0.7,
                reasons=entry_signal.reasons + ["ai_consensus: disagree, reduced 0.7x"],
            )
            _reason_code = "kept_disagree_softened"
        elif consensus == "disagree" and bypass_consensus:
            _reason_code = "kept_disagree_consensus_bypass"
        elif consensus == "bullish":
            _reason_code = "kept_bullish_consensus"
        elif consensus == "neutral":
            _reason_code = "kept_neutral_consensus"
        # / else: consensus None or unknown -> reason stays "passthrough"

        # / telemetry log (bug f): per-evaluation consensus state for observability
        logger.info(
            "consensus_filter_decision",
            strategy_id=strategy.strategy_id,
            symbol=symbol,
            decision_time=datetime.now(timezone.utc).isoformat(),
            groq_consensus=raw_details.get("llm_signal_groq") if raw_details else None,
            deepseek_consensus=raw_details.get("llm_signal_deepseek") if raw_details else None,
            combined_consensus=consensus,
            raw_signal_strength=_pre_filter_strength,
            signal_kept=_signal_kept,
            reason_code=_reason_code,
            symbol_trend=symbol_trend,
            bypass_consensus=bypass_consensus,
            regime=regime,
        )

        if _blocked_return:
            if stats is not None:
                stats["blocked_consensus"] += 1
                stats["near_misses"].append({
                    "symbol": symbol,
                    "raw_strength": _pre_filter_strength,
                    "block_reason": f"bearish consensus (trend={symbol_trend})",
                    "symbol_trend": symbol_trend,
                    "consensus_debug": {
                        "groq_consensus": raw_details.get("llm_signal_groq") if raw_details else None,
                        "deepseek_consensus": raw_details.get("llm_signal_deepseek") if raw_details else None,
                        "combined_consensus": consensus,
                        "raw_signal_strength": _pre_filter_strength,
                        "reason_code": _reason_code,
                    },
                })
            return None

        # / smooth with particle filter
        signal_threshold_override = strategy.config.get("signal_threshold_override")
        threshold = (
            signal_threshold_override
            if signal_threshold_override is not None
            else SIGNAL_THRESHOLD
        )
        smoothed_strength = self._smooth_signal(symbol, entry_signal.strength)

        # / ml signal modifier (lightgbm probability)
        if len(df) >= 252:
            try:
                from src.quant.ml_signals import train_and_predict
                indicators_dict = {}
                if analysis_data:
                    for attr in ("macro_score", "analyst_consensus", "short_pct_float", "iv_rank", "hurst"):
                        val = getattr(analysis_data, attr, None)
                        if val is not None:
                            indicators_dict[attr] = val
                ml_pred = await train_and_predict(df, indicators=indicators_dict or None)
                if ml_pred and ml_pred.probability is not None:
                    # / probability > 0.6 = boost, < 0.4 = penalty
                    if ml_pred.probability > 0.6:
                        smoothed_strength = min(1.0, smoothed_strength * (1.0 + (ml_pred.probability - 0.5)))
                    elif ml_pred.probability < 0.4:
                        smoothed_strength = smoothed_strength * (0.5 + ml_pred.probability)
                    logger.debug("ml_signal_applied", symbol=symbol, ml_prob=ml_pred.probability, adjusted=smoothed_strength)
                    try:
                        from src.quant.ml_signals import store_ml_prediction
                        await store_ml_prediction(pool, ml_pred)
                    except Exception:
                        pass
            except Exception as exc:
                logger.debug("ml_signal_failed", symbol=symbol, error=str(exc))

        if smoothed_strength < threshold:
            logger.debug(
                "signal_below_threshold",
                symbol=symbol, raw=entry_signal.strength,
                smoothed=smoothed_strength,
            )
            if stats is not None:
                stats["blocked_threshold"] += 1
                stats["near_misses"].append({
                    "symbol": symbol, "raw_strength": entry_signal.strength,
                    "block_reason": f"threshold ({smoothed_strength:.2f} < {threshold})",
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

    async def _fetch_intraday_df(
        self, pool, symbol: str, min_bars: int = 20,
    ) -> pd.DataFrame | None:
        # / fetch 2h intraday bars, cached per cycle
        if symbol in self._intraday_cache:
            return self._intraday_cache[symbol]

        try:
            rows = await tools.fetch_intraday_ohlcv(
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
        except Exception:
            self._intraday_cache[symbol] = None
            return None

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
            await tools.store_computed_indicators(pool, symbol, indicators)

            # / compute and store 2h intraday indicators
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
                    await tools.store_computed_indicators(pool, symbol, intraday_ind, timeframe="1Hour")
                except Exception as exc2:
                    logger.debug("intraday_indicator_compute_failed", symbol=symbol, error=str(exc2))

            # / compute and store ict indicators (fvg, order blocks, structure breaks)
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
                await tools.store_ict_indicators(pool, symbol, ict_data)
            except Exception as exc3:
                logger.debug("ict_indicator_compute_failed", symbol=symbol, error=str(exc3))
        except Exception as exc:
            logger.debug("indicator_compute_failed", symbol=symbol, error=str(exc))

    def _smooth_signal(self, symbol: str, raw_strength: float) -> float:
        # / use particle filter to smooth noisy entry signals
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
        # / classify individual symbol trend from price vs sma50
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
        # / check exit conditions using strategy_positions (knows who owns what)
        signals: list[dict] = []
        checked_symbols: set[str] = set()

        active = (
            strategy_pool.list_by_status("paper_trading")
            + strategy_pool.list_by_status("live")
        )

        for entry in active:
            strategy = entry.strategy
            # / get this strategy's positions from db
            strat_positions = await tools.get_strategy_positions(pool, strategy_id=strategy.strategy_id)
            for sp in strat_positions:
                checked_symbols.add(sp["symbol"])
                exit_sig = await self._eval_exit(pool, strategy, sp)
                if exit_sig:
                    signals.append(exit_sig)

        # / also check untracked positions using the first active strategy's exit rules
        if active:
            default_strategy = active[0].strategy
            untracked = await tools.get_strategy_positions(pool, strategy_id="untracked")
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
        # / phase 6 step 12: evaluate the first partial_exits tier.
        # / strategy JSON shape:
        # /   exit_conditions.partial_exits: [{"trigger": "take_profit_pct", "threshold": 0.05, "fraction": 0.5}]
        # / returns None when no tier triggers or the position already consumed a tier.
        try:
            if sp.get("partial_exit_fired"):
                return None
            cfg = strategy.config if hasattr(strategy, "config") else {}
            tiers = (cfg.get("exit_conditions", {}) or {}).get("partial_exits") or []
            if not tiers:
                return None
            tier = tiers[0]  # / only first tier for now; future tiers can fire on re-entry
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
            # / future triggers: "take_profit_1r" with stop distance input, etc.
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
        # / evaluate exit for a single position
        try:
            df = await self._fetch_market_df(pool, sp["symbol"])
            if df is None:
                return None

            # / phase 6 step 12: partial-exit tier check before full-exit evaluation.
            # / if the strategy's exit_conditions.partial_exits[0] trigger has fired
            # / and partial_exit_fired is still false, sell `fraction` of the position.
            partial_sig = self._eval_partial_exit(strategy, sp, df, override_strategy_id)
            if partial_sig is not None:
                signal_id = await tools.store_trade_signal(
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
                # / mark the position so we don't re-fire next cycle; best-effort
                try:
                    await tools.mark_partial_exit_fired(
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

            # / use position's updated_at as entry proxy, fallback to oldest bar
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
                signal_id = await tools.store_trade_signal(
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

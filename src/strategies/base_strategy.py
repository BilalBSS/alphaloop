# / abstract strategy interface — all strategies implement this
# / strategies wrap json configs, use indicators + analysis to decide entry/exit
# / two layers: fundamental filters (always on) + technical signals (varies)

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class EntrySignal:
    should_enter: bool
    strength: float = 0.0  # 0.0 to 1.0
    reasons: list[str] = field(default_factory=list)


@dataclass
class ExitSignal:
    should_exit: bool
    reason: str = ""


@dataclass
class PositionSizeResult:
    qty: float
    pct_of_portfolio: float
    method: str


@dataclass
class AnalysisData:
    # / fundamental data from analysis engine
    pe_ratio: float | None = None
    pe_forward: float | None = None
    ps_ratio: float | None = None
    peg_ratio: float | None = None
    revenue_growth: float | None = None
    fcf_margin: float | None = None
    debt_to_equity: float | None = None
    sector_pe_avg: float | None = None
    sector_ps_avg: float | None = None
    dcf_upside: float | None = None
    insider_net_buy_ratio: float | None = None
    earnings_surprise_pct: float | None = None
    consecutive_beats: int = 0
    fundamental_score: float | None = None  # 0-100 composite
    # / crypto-specific fields (phase 8)
    nvt_ratio: float | None = None
    funding_rate: float | None = None
    exchange_flow_ratio: float | None = None
    news_sentiment_score: float | None = None
    ai_consensus: str | None = None  # bullish, bearish, neutral, disagree
    regime: str | None = None


class StrategyInterface(ABC):
    @abstractmethod
    def should_enter(
        self,
        symbol: str,
        market_data: pd.DataFrame,
        analysis: AnalysisData | None = None,
        intraday_df: pd.DataFrame | None = None,
    ) -> EntrySignal:
        # / evaluate entry conditions against current market state
        # / market_data: daily ohlcv dataframe with columns [open, high, low, close, volume]
        # / analysis: fundamental data (required for fundamental-gated strategies)
        # / intraday_df: optional 2h ohlcv for multi-timeframe signal evaluation
        ...

    @abstractmethod
    def should_exit(
        self,
        symbol: str,
        market_data: pd.DataFrame,
        entry_price: float,
        entry_date: pd.Timestamp,
        current_bar_idx: int,
    ) -> ExitSignal:
        # / evaluate exit conditions for an open position
        # / entry_price: the price at which we entered
        # / entry_date: when we entered (for time-based exits)
        # / current_bar_idx: index into market_data for current bar
        ...

    @abstractmethod
    def position_size(
        self,
        equity: float,
        price: float,
        strength: float,
    ) -> PositionSizeResult:
        # / determine position size given account equity and signal strength
        # / returns qty of shares to buy
        ...

    @property
    @abstractmethod
    def strategy_id(self) -> str:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def config(self) -> dict[str, Any]:
        ...


class ConfigDrivenStrategy(StrategyInterface):
    # / concrete strategy that evaluates entry/exit from a json config
    # / loader-created strategy
    # / the evolution engine mutates the config, not this code

    def __init__(self, config: dict[str, Any]):
        self._config = config
        self._id = config["id"]
        self._name = config["name"]
        # / normalize universe: accept list (legacy) or string ref
        raw_universe = config.get("universe", "all")
        if isinstance(raw_universe, list):
            self._universe_ref = ",".join(raw_universe) if raw_universe else "all"
        else:
            self._universe_ref = raw_universe or "all"
        self._fundamental_filters = config.get("fundamental_filters", {})
        self._entry_conditions = config.get("entry_conditions", {})
        self._exit_conditions = config.get("exit_conditions", {})
        self._position_sizing = config.get("position_sizing", {})
        self._bear_market_overrides = config.get("bear_market_overrides", {})
        self._requires_fundamentals = bool(self._fundamental_filters)

    @property
    def strategy_id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    @property
    def universe_ref(self) -> str:
        # / the raw universe reference from config ("all", "all_stocks", or comma-separated)
        return self._universe_ref

    def resolve_universe(self, available_symbols: list[str] | None = None) -> list[str]:
        # / precedence: symbol > sector > universe ref
        # / tier-2/3 strategies target a single symbol
        symbol = self._config.get("symbol")
        if symbol:
            return [symbol]
        # / sector strategies target all symbols in the sector
        sector = self._config.get("sector")
        if sector:
            from src.data.symbols import get_sector_symbols
            syms = get_sector_symbols(sector)
            if syms:
                return syms
        # / fallback to universe ref (existing behavior)
        from src.data.symbols import resolve_universe
        return resolve_universe(self._universe_ref, available_symbols)

    @property
    def requires_fundamentals(self) -> bool:
        return self._requires_fundamentals

    def _get_active_filters(self, analysis) -> dict:
        # / merge bear market overrides when regime is bear
        filters = dict(self._fundamental_filters)
        if (
            analysis is not None
            and getattr(analysis, "regime", None) == "bear"
            and self._bear_market_overrides
        ):
            override_ff = self._bear_market_overrides.get("fundamental_filters", {})
            for key, value in override_ff.items():
                if value is not None:
                    filters[key] = value
        return filters

    def get_effective_bypass_consensus(self, regime: str | None = None) -> bool:
        # / check if consensus should be bypassed, with bear override
        base = self._config.get("bypass_consensus", False)
        if regime == "bear" and self._bear_market_overrides:
            override = self._bear_market_overrides.get("bypass_consensus")
            if override is not None:
                return override
        return base

    def should_enter(
        self,
        symbol: str,
        market_data: pd.DataFrame,
        analysis: AnalysisData | None = None,
        intraday_df: pd.DataFrame | None = None,
    ) -> EntrySignal:
        if len(market_data) < 2:
            return EntrySignal(should_enter=False, reasons=["insufficient data"])

        # / check fundamental filters first (if required)
        if self._requires_fundamentals:
            if analysis is None:
                return EntrySignal(should_enter=False, reasons=["no fundamental data"])
            active_filters = self._get_active_filters(analysis)
            passed, fundamental_reasons = self._check_fundamentals(analysis, active_filters)
            if not passed:
                return EntrySignal(should_enter=False, reasons=fundamental_reasons)

        # / check technical entry conditions
        passed, strength, tech_reasons = self._check_entry_technicals(market_data, intraday_df)
        if not passed:
            return EntrySignal(should_enter=False, reasons=tech_reasons)

        reasons = tech_reasons
        if self._requires_fundamentals:
            reasons = ["fundamentals passed"] + reasons

        return EntrySignal(should_enter=True, strength=strength, reasons=reasons)

    def should_exit(
        self,
        symbol: str,
        market_data: pd.DataFrame,
        entry_price: float,
        entry_date: pd.Timestamp,
        current_bar_idx: int,
    ) -> ExitSignal:
        if current_bar_idx >= len(market_data):
            return ExitSignal(should_exit=False)

        current = market_data.iloc[current_bar_idx]
        current_price = float(current["close"])

        # / check time exit first
        time_exit = self._exit_conditions.get("time_exit")
        if time_exit:
            max_days = time_exit.get("max_holding_days", 9999)
            current_date = market_data.index[current_bar_idx]
            if hasattr(current_date, "to_pydatetime"):
                current_date = current_date.to_pydatetime()
            if hasattr(entry_date, "to_pydatetime"):
                entry_date_dt = entry_date.to_pydatetime()
            else:
                entry_date_dt = entry_date
            days_held = (current_date - entry_date_dt).days
            if days_held >= max_days:
                return ExitSignal(should_exit=True, reason=f"time exit: {days_held} days >= {max_days}")

        # / check stop loss
        stop_loss = self._exit_conditions.get("stop_loss")
        if stop_loss:
            exit_signal = self._check_stop_loss(
                stop_loss, market_data, entry_price, current_bar_idx, entry_date,
            )
            if exit_signal.should_exit:
                return exit_signal

        # / check take profit
        take_profit = self._exit_conditions.get("take_profit")
        if take_profit:
            exit_signal = self._check_take_profit(
                take_profit, market_data, current_bar_idx,
            )
            if exit_signal.should_exit:
                return exit_signal

        return ExitSignal(should_exit=False)

    def position_size(
        self,
        equity: float,
        price: float,
        strength: float,
    ) -> PositionSizeResult:
        method = self._position_sizing.get("method", "fixed_pct")
        max_pct = self._position_sizing.get("max_position_pct", 0.08)

        if method == "kelly_fraction":
            kelly_f = self._position_sizing.get("kelly_fraction", 0.25)
            # / kelly fraction scaled by signal strength, capped at max_pct
            pct = min(kelly_f * strength, max_pct)
        elif method == "fixed_pct":
            pct = max_pct
        elif method == "strength_scaled":
            # / scale position by signal strength
            pct = max_pct * strength
        else:
            pct = max_pct

        # / ensure minimum position of 1 share worth
        if price <= 0:
            return PositionSizeResult(qty=0, pct_of_portfolio=0, method=method)

        position_value = equity * pct
        qty = int(position_value / price)  # / whole shares only
        actual_pct = (qty * price) / equity if equity > 0 else 0

        return PositionSizeResult(qty=qty, pct_of_portfolio=actual_pct, method=method)

    def _check_fundamentals(self, analysis: AnalysisData, filters=None) -> tuple[bool, list[str]]:
        # / evaluate fundamental filters from config against analysis data
        # / strict_data=true (default): reject if data unavailable
        # / strict_data=false: skip filter when data is missing
        filters = filters or self._fundamental_filters
        reasons: list[str] = []
        strict = filters.get("strict_data", True)

        # / pe ratio max
        pe_max = filters.get("pe_ratio_max")
        if pe_max is not None:
            if analysis.pe_ratio is None:
                if strict:
                    return False, ["pe_ratio data unavailable"]
            elif analysis.pe_ratio > pe_max:
                reasons.append(f"pe {analysis.pe_ratio:.1f} > max {pe_max}")
                return False, reasons

        # / pe vs sector
        pe_vs = filters.get("pe_vs_sector")
        if pe_vs == "below_average":
            if analysis.pe_ratio is None or analysis.sector_pe_avg is None:
                if strict:
                    return False, ["pe or sector_pe data unavailable"]
            elif analysis.pe_ratio > analysis.sector_pe_avg:
                reasons.append(f"pe {analysis.pe_ratio:.1f} above sector avg {analysis.sector_pe_avg:.1f}")
                return False, reasons

        # / revenue growth min
        rev_min = filters.get("revenue_growth_min")
        if rev_min is not None:
            if analysis.revenue_growth is None:
                if strict:
                    return False, ["revenue_growth data unavailable"]
            elif analysis.revenue_growth < rev_min:
                reasons.append(f"revenue growth {analysis.revenue_growth:.2%} < min {rev_min:.2%}")
                return False, reasons

        # / fcf margin min
        fcf_min = filters.get("fcf_margin_min")
        if fcf_min is not None:
            if analysis.fcf_margin is None:
                if strict:
                    return False, ["fcf_margin data unavailable"]
            elif analysis.fcf_margin < fcf_min:
                reasons.append(f"fcf margin {analysis.fcf_margin:.2%} < min {fcf_min:.2%}")
                return False, reasons

        # / debt to equity max
        de_max = filters.get("debt_to_equity_max")
        if de_max is not None:
            if analysis.debt_to_equity is None:
                if strict:
                    return False, ["debt_to_equity data unavailable"]
            elif analysis.debt_to_equity > de_max:
                reasons.append(f"d/e {analysis.debt_to_equity:.2f} > max {de_max}")
                return False, reasons

        # / dcf upside min
        dcf_min = filters.get("dcf_upside_min")
        if dcf_min is not None:
            if analysis.dcf_upside is None:
                if strict:
                    return False, ["dcf_upside data unavailable"]
            elif analysis.dcf_upside < dcf_min:
                reasons.append(f"dcf upside {analysis.dcf_upside:.2%} < min {dcf_min:.2%}")
                return False, reasons

        # / insider buying recent
        insider_req = filters.get("insider_buying_recent")
        if insider_req is True:
            if analysis.insider_net_buy_ratio is None:
                if strict:
                    return False, ["insider_activity data unavailable"]
            elif analysis.insider_net_buy_ratio <= 0:
                return False, [f"no recent insider buying (ratio={analysis.insider_net_buy_ratio:.2f})"]

        # / crypto filters (phase 8)
        nvt_max = filters.get("nvt_max")
        if nvt_max is not None and analysis.nvt_ratio is not None:
            if analysis.nvt_ratio > nvt_max:
                return False, [f"nvt {analysis.nvt_ratio:.1f} > max {nvt_max}"]

        funding_max = filters.get("funding_rate_max")
        if funding_max is not None and analysis.funding_rate is not None:
            if abs(analysis.funding_rate) > funding_max:
                return False, [f"funding rate {analysis.funding_rate:.4f} exceeds max {funding_max}"]

        sentiment_min = filters.get("news_sentiment_min")
        if sentiment_min is not None and analysis.news_sentiment_score is not None:
            if analysis.news_sentiment_score < sentiment_min:
                return False, [f"sentiment {analysis.news_sentiment_score:.2f} < min {sentiment_min}"]

        return True, ["fundamentals passed"]

    def _check_entry_technicals(
        self, market_data: pd.DataFrame, intraday_df: pd.DataFrame | None = None,
    ) -> tuple[bool, float, list[str]]:
        # / evaluate technical entry conditions from config
        signals = self._entry_conditions.get("signals", [])
        operator = self._entry_conditions.get("operator", "AND")

        if not signals:
            return True, 1.0, ["no technical conditions"]

        results: list[tuple[bool, float, str]] = []
        for sig in signals:
            # / route to intraday df if signal specifies non-daily timeframe
            tf = sig.get("timeframe")
            if tf and tf.lower() not in ("1d", "daily", "1day"):
                if intraday_df is not None and len(intraday_df) >= 14:
                    passed, strength, reason = self._evaluate_signal(sig, intraday_df)
                    results.append((passed, strength, f"[{tf}] {reason}"))
                else:
                    results.append((False, 0.0, f"{sig.get('indicator', '?')} skipped: no {tf} data"))
                continue
            passed, strength, reason = self._evaluate_signal(sig, market_data)
            results.append((passed, strength, reason))

        if operator == "AND":
            all_passed = all(r[0] for r in results)
            if not all_passed:
                failed = [r[2] for r in results if not r[0]]
                return False, 0.0, failed
            avg_strength = sum(r[1] for r in results) / len(results)
            reasons = [r[2] for r in results]
            return True, avg_strength, reasons
        else:  # OR
            any_passed = any(r[0] for r in results)
            if not any_passed:
                return False, 0.0, [r[2] for r in results]
            passed_results = [r for r in results if r[0]]
            max_strength = max(r[1] for r in passed_results)
            reasons = [r[2] for r in passed_results]
            return True, max_strength, reasons

    def _eval_bollinger(
        self, sig: dict[str, Any], market_data: pd.DataFrame,
    ) -> tuple[bool, float, str]:
        from src.indicators.volatility import bollinger_bands
        close = market_data["close"]
        condition = sig.get("condition", "")
        period = sig.get("lookback", sig.get("period", 20))
        std = sig.get("std_dev", 2.0)
        bb = bollinger_bands(close, period=period, std_dev=std)
        last_close = float(close.iloc[-1])
        if condition == "price_below_lower":
            last_lower = float(bb.lower.iloc[-1])
            passed = last_close < last_lower
            strength = max(0, min(1, (last_lower - last_close) / (last_lower * 0.01 + 1e-9)))
            return passed, strength if passed else 0.0, f"bb: close={last_close:.2f} {'<' if passed else '>='} lower={last_lower:.2f}"
        elif condition == "price_above_upper":
            last_upper = float(bb.upper.iloc[-1])
            passed = last_close > last_upper
            strength = max(0, min(1, (last_close - last_upper) / (last_upper * 0.01 + 1e-9)))
            return passed, strength if passed else 0.0, f"bb: close={last_close:.2f} {'>' if passed else '<='} upper={last_upper:.2f}"
        elif condition == "price_above_middle":
            last_mid = float(bb.middle.iloc[-1])
            passed = last_close > last_mid
            return passed, 0.5 if passed else 0.0, f"bb: close={last_close:.2f} {'>' if passed else '<='} middle={last_mid:.2f}"
        elif condition == "price_below_upper":
            last_upper = float(bb.upper.iloc[-1])
            passed = last_close < last_upper
            return passed, 0.5 if passed else 0.0, f"bb: close={last_close:.2f} {'<' if passed else '>='} upper={last_upper:.2f}"
        return False, 0.0, f"unknown bb condition: {condition}"

    def _eval_rsi(
        self, sig: dict[str, Any], market_data: pd.DataFrame,
    ) -> tuple[bool, float, str]:
        from src.indicators.momentum import rsi as rsi_fn
        close = market_data["close"]
        condition = sig.get("condition", "")
        period = sig.get("period", 14)
        threshold = sig.get("threshold", 30)
        rsi_val = rsi_fn(close, period=period)
        last_rsi = float(rsi_val.dropna().iloc[-1]) if not rsi_val.dropna().empty else 50.0
        if condition == "below":
            passed = last_rsi < threshold
            strength = max(0, min(1, (threshold - last_rsi) / threshold)) if passed else 0.0
            return passed, strength, f"rsi={last_rsi:.1f} {'<' if passed else '>='} {threshold}"
        elif condition == "above":
            passed = last_rsi > threshold
            strength = max(0, min(1, (last_rsi - threshold) / (100 - threshold))) if passed else 0.0
            return passed, strength, f"rsi={last_rsi:.1f} {'>' if passed else '<='} {threshold}"
        return False, 0.0, f"unknown rsi condition: {condition}"

    def _eval_macd(
        self, sig: dict[str, Any], market_data: pd.DataFrame,
    ) -> tuple[bool, float, str]:
        from src.indicators.trend import macd as macd_fn
        close = market_data["close"]
        condition = sig.get("condition", "")
        result = macd_fn(close)
        last_hist = float(result.histogram.dropna().iloc[-1]) if not result.histogram.dropna().empty else 0.0
        prev_hist = float(result.histogram.dropna().iloc[-2]) if len(result.histogram.dropna()) >= 2 else 0.0
        if condition == "crossover_bullish":
            passed = prev_hist < 0 and last_hist >= 0
            return passed, 0.7 if passed else 0.0, f"macd histogram: {prev_hist:.4f} -> {last_hist:.4f}"
        elif condition == "crossover_bearish":
            passed = prev_hist > 0 and last_hist <= 0
            return passed, 0.7 if passed else 0.0, f"macd histogram: {prev_hist:.4f} -> {last_hist:.4f}"
        elif condition == "positive":
            passed = last_hist > 0
            return passed, 0.5 if passed else 0.0, f"macd histogram={last_hist:.4f}"
        return False, 0.0, f"unknown macd condition: {condition}"

    def _eval_volume(
        self, sig: dict[str, Any], market_data: pd.DataFrame,
    ) -> tuple[bool, float, str]:
        volume = market_data["volume"]
        condition = sig.get("condition", "")
        period = sig.get("period", 20)
        multiplier = sig.get("multiplier", 1.5)
        avg_vol = float(volume.rolling(window=period, min_periods=period).mean().iloc[-1])
        last_vol = float(volume.iloc[-1])
        if condition == "above_average":
            passed = last_vol > avg_vol * multiplier
            strength = min(1, last_vol / (avg_vol * multiplier)) if avg_vol > 0 else 0.0
            return passed, strength if passed else 0.0, f"vol={last_vol:.0f} {'>' if passed else '<='} {multiplier}x avg={avg_vol:.0f}"
        return False, 0.0, f"unknown volume condition: {condition}"

    def _eval_sma(
        self, sig: dict[str, Any], market_data: pd.DataFrame,
    ) -> tuple[bool, float, str]:
        from src.indicators.trend import sma as sma_fn
        close = market_data["close"]
        condition = sig.get("condition", "")
        period = sig.get("period", 50)
        sma_val = sma_fn(close, period=period)
        last_close = float(close.iloc[-1])
        last_sma = float(sma_val.iloc[-1])
        if condition == "price_above":
            passed = last_close > last_sma
            return passed, 0.5 if passed else 0.0, f"close={last_close:.2f} {'>' if passed else '<='} sma{period}={last_sma:.2f}"
        elif condition == "price_below":
            passed = last_close < last_sma
            return passed, 0.5 if passed else 0.0, f"close={last_close:.2f} {'<' if passed else '>='} sma{period}={last_sma:.2f}"
        return False, 0.0, f"unknown sma condition: {condition}"

    def _eval_adx(
        self, sig: dict[str, Any], market_data: pd.DataFrame,
    ) -> tuple[bool, float, str]:
        from src.indicators.trend import adx as adx_fn
        close = market_data["close"]
        high = market_data["high"]
        low = market_data["low"]
        condition = sig.get("condition", "")
        period = sig.get("period", 14)
        threshold = sig.get("threshold", 25)
        adx_val = adx_fn(high, low, close, period=period)
        last_adx = float(adx_val.dropna().iloc[-1]) if not adx_val.dropna().empty else 0.0
        if condition == "above":
            passed = last_adx > threshold
            return passed, min(1, last_adx / 50) if passed else 0.0, f"adx={last_adx:.1f} {'>' if passed else '<='} {threshold}"
        elif condition == "below":
            passed = last_adx < threshold
            return passed, 0.5 if passed else 0.0, f"adx={last_adx:.1f} {'<' if passed else '>='} {threshold}"
        return False, 0.0, f"unknown adx condition: {condition}"

    def _eval_atr(
        self, sig: dict[str, Any], market_data: pd.DataFrame,
    ) -> tuple[bool, float, str]:
        from src.indicators.volatility import atr as atr_fn
        close = market_data["close"]
        high = market_data["high"]
        low = market_data["low"]
        condition = sig.get("condition", "")
        period = sig.get("period", 14)
        threshold = sig.get("threshold", 0)
        atr_val = atr_fn(high, low, close, period=period)
        last_atr = float(atr_val.dropna().iloc[-1]) if not atr_val.dropna().empty else 0.0
        last_close = float(close.iloc[-1])
        atr_pct = last_atr / last_close if last_close > 0 else 0
        if condition == "above":
            passed = atr_pct > threshold
            return passed, 0.5 if passed else 0.0, f"atr%={atr_pct:.4f} {'>' if passed else '<='} {threshold}"
        elif condition == "below":
            passed = atr_pct < threshold
            return passed, 0.5 if passed else 0.0, f"atr%={atr_pct:.4f} {'<' if passed else '>='} {threshold}"
        # / no condition specified: informational only, always passes
        return True, 0.5, f"atr={last_atr:.2f} ({atr_pct:.2%} of price)"

    def _eval_stochastic(
        self, sig: dict[str, Any], market_data: pd.DataFrame,
    ) -> tuple[bool, float, str]:
        from src.indicators.momentum import stochastic as stoch_fn
        close = market_data["close"]
        high = market_data["high"]
        low = market_data["low"]
        condition = sig.get("condition", "")
        period = sig.get("period", 14)
        threshold = sig.get("threshold", 20)
        result = stoch_fn(high, low, close, k_period=period)
        last_k = float(result.k.dropna().iloc[-1]) if not result.k.dropna().empty else 50.0
        if condition == "below":
            passed = last_k < threshold
            return passed, max(0, min(1, (threshold - last_k) / threshold)) if passed else 0.0, f"stoch %k={last_k:.1f} {'<' if passed else '>='} {threshold}"
        elif condition == "above":
            passed = last_k > threshold
            return passed, max(0, min(1, (last_k - threshold) / (100 - threshold))) if passed else 0.0, f"stoch %k={last_k:.1f} {'>' if passed else '<='} {threshold}"
        return False, 0.0, f"unknown stochastic condition: {condition}"

    def _eval_fvg(
        self, sig: dict[str, Any], market_data: pd.DataFrame,
    ) -> tuple[bool, float, str]:
        from src.indicators.structure import fair_value_gaps
        close = market_data["close"]
        high = market_data["high"]
        low = market_data["low"]
        condition = sig.get("condition", "")
        result = fair_value_gaps(high, low, close)
        last_sig = int(result.signal.iloc[-1])
        if condition == "bullish":
            passed = last_sig == 1
            return passed, 0.7 if passed else 0.0, f"fvg signal={last_sig}"
        elif condition == "bearish":
            passed = last_sig == -1
            return passed, 0.7 if passed else 0.0, f"fvg signal={last_sig}"
        passed = last_sig != 0
        return passed, 0.6 if passed else 0.0, f"fvg signal={last_sig}"

    def _eval_order_block(
        self, sig: dict[str, Any], market_data: pd.DataFrame,
    ) -> tuple[bool, float, str]:
        from src.indicators.structure import order_blocks
        close = market_data["close"]
        high = market_data["high"]
        low = market_data["low"]
        condition = sig.get("condition", "")
        open_ = market_data["open"]
        result = order_blocks(high, low, close, open_)
        last_sig = int(result.signal.iloc[-1])
        if condition == "bullish":
            passed = last_sig == 1
        elif condition == "bearish":
            passed = last_sig == -1
        else:
            passed = last_sig != 0
        return passed, 0.7 if passed else 0.0, f"ob signal={last_sig}"

    def _eval_structure_break(
        self, sig: dict[str, Any], market_data: pd.DataFrame,
    ) -> tuple[bool, float, str]:
        from src.indicators.structure import structure_breaks
        close = market_data["close"]
        high = market_data["high"]
        low = market_data["low"]
        condition = sig.get("condition", "")
        lookback = sig.get("lookback", 5)
        result = structure_breaks(high, low, close, swing_lookback=lookback)
        last_sig = int(result.signal.iloc[-1])
        if condition == "bullish":
            passed = last_sig == 1
        elif condition == "bearish":
            passed = last_sig == -1
        else:
            passed = last_sig != 0
        return passed, 0.8 if passed else 0.0, f"structure break={last_sig}"

    def _eval_pivot_points(
        self, sig: dict[str, Any], market_data: pd.DataFrame,
    ) -> tuple[bool, float, str]:
        from src.indicators.support_resistance import pivot_points
        close = market_data["close"]
        high = market_data["high"]
        low = market_data["low"]
        condition = sig.get("condition", "")
        pp = pivot_points(float(high.iloc[-2]), float(low.iloc[-2]), float(close.iloc[-2]))
        last_close = float(close.iloc[-1])
        if condition == "above_r1":
            passed = last_close > pp.r1
            return passed, 0.6 if passed else 0.0, f"close={last_close:.2f} vs r1={pp.r1:.2f}"
        elif condition == "below_s1":
            passed = last_close < pp.s1
            return passed, 0.6 if passed else 0.0, f"close={last_close:.2f} vs s1={pp.s1:.2f}"
        passed = last_close < pp.pivot
        return passed, 0.5 if passed else 0.0, f"close={last_close:.2f} vs pivot={pp.pivot:.2f}"

    def _eval_fibonacci(
        self, sig: dict[str, Any], market_data: pd.DataFrame,
    ) -> tuple[bool, float, str]:
        from src.indicators.support_resistance import fibonacci_retracement
        close = market_data["close"]
        high = market_data["high"]
        low = market_data["low"]
        condition = sig.get("condition", "")
        lookback = sig.get("lookback", 50)
        fib = fibonacci_retracement(high, low, lookback=lookback)
        last_close = float(close.iloc[-1])
        level = sig.get("level", 0.618)
        fib_map = {0.236: fib.level_236, 0.382: fib.level_382, 0.5: fib.level_500, 0.618: fib.level_618, 0.786: fib.level_786}
        target = fib_map.get(level, fib.level_618)
        tolerance = abs(target - fib.swing_low) * 0.02
        if condition == "near_level":
            passed = abs(last_close - target) <= tolerance
            return passed, 0.7 if passed else 0.0, f"close={last_close:.2f} near fib {level}={target:.2f}"
        passed = last_close <= target
        return passed, 0.5 if passed else 0.0, f"close={last_close:.2f} vs fib {level}={target:.2f}"

    def _eval_sr_zone(
        self, sig: dict[str, Any], market_data: pd.DataFrame,
    ) -> tuple[bool, float, str]:
        from src.indicators.support_resistance import sr_zones_series
        close = market_data["close"]
        high = market_data["high"]
        low = market_data["low"]
        condition = sig.get("condition", "")
        sr = sr_zones_series(close, high, low)
        last_sr = float(sr.iloc[-1])
        threshold_val = sig.get("threshold", 0.02)
        if condition == "near_support":
            passed = last_sr < 0 and abs(last_sr) < threshold_val
            return passed, 0.6 if passed else 0.0, f"sr distance={last_sr:.4f}"
        elif condition == "near_resistance":
            passed = last_sr > 0 and abs(last_sr) < threshold_val
            return passed, 0.6 if passed else 0.0, f"sr distance={last_sr:.4f}"
        passed = abs(last_sr) < threshold_val
        return passed, 0.5 if passed else 0.0, f"sr distance={last_sr:.4f}"

    _SIGNAL_HANDLERS: dict[str, Any] = {
        "bollinger_bands": _eval_bollinger,
        "rsi": _eval_rsi,
        "macd": _eval_macd,
        "volume": _eval_volume,
        "sma": _eval_sma,
        "adx": _eval_adx,
        "atr": _eval_atr,
        "stochastic": _eval_stochastic,
        "fair_value_gap": _eval_fvg,
        "order_block": _eval_order_block,
        "structure_break": _eval_structure_break,
        "pivot_points": _eval_pivot_points,
        "fibonacci": _eval_fibonacci,
        "sr_zone": _eval_sr_zone,
    }

    def _evaluate_signal(
        self, sig: dict[str, Any], market_data: pd.DataFrame,
    ) -> tuple[bool, float, str]:
        # / evaluate a single technical signal condition
        indicator = sig.get("indicator", "")
        try:
            handler = self._SIGNAL_HANDLERS.get(indicator)
            if handler is None:
                # / unknown indicator — skip gracefully
                return False, 0.0, f"unknown indicator: {indicator}"
            return handler(self, sig, market_data)
        except (IndexError, KeyError, ValueError) as e:
            logger.warning("signal_evaluation_error", indicator=indicator, error=str(e))
            return False, 0.0, f"error evaluating {indicator}: {e}"

    def _check_stop_loss(
        self,
        stop_config: dict[str, Any],
        market_data: pd.DataFrame,
        entry_price: float,
        current_bar_idx: int,
        entry_date: pd.Timestamp | None = None,
    ) -> ExitSignal:
        current_price = float(market_data.iloc[current_bar_idx]["close"])
        stop_type = stop_config.get("type", "fixed_pct")

        if stop_type == "fixed_pct":
            pct = stop_config.get("pct", 0.05)
            stop_price = entry_price * (1 - pct)
            if current_price <= stop_price:
                return ExitSignal(should_exit=True, reason=f"stop loss: price {current_price:.2f} <= {stop_price:.2f} ({pct:.0%})")

        elif stop_type == "atr_trailing":
            from src.indicators.volatility import atr as atr_fn
            period = stop_config.get("period", 14)
            multiplier = stop_config.get("multiplier", 2.0)
            high = market_data["high"]
            low = market_data["low"]
            close = market_data["close"]
            atr_val = atr_fn(high, low, close, period=period)

            # / trailing stop: highest close since entry minus atr * multiplier
            # / scope to data from entry date onward (not pre-entry peaks)
            data_slice = market_data.iloc[:current_bar_idx + 1]
            if entry_date is not None:
                data_slice = data_slice[data_slice.index >= entry_date]
            highest_since_entry = float(data_slice["close"].max())
            current_atr = float(atr_val.iloc[current_bar_idx]) if not pd.isna(atr_val.iloc[current_bar_idx]) else 0
            stop_price = highest_since_entry - multiplier * current_atr
            if current_price <= stop_price and current_atr > 0:
                return ExitSignal(
                    should_exit=True,
                    reason=f"atr trailing stop: price {current_price:.2f} <= {stop_price:.2f} (high={highest_since_entry:.2f} - {multiplier}*atr={current_atr:.2f})",
                )

        return ExitSignal(should_exit=False)

    def _check_take_profit(
        self,
        tp_config: dict[str, Any],
        market_data: pd.DataFrame,
        current_bar_idx: int,
    ) -> ExitSignal:
        indicator = tp_config.get("indicator", "")
        condition = tp_config.get("condition", "")
        close = market_data["close"]
        current_price = float(close.iloc[current_bar_idx])

        if indicator == "bollinger_bands":
            from src.indicators.volatility import bollinger_bands
            period = tp_config.get("lookback", tp_config.get("period", 20))
            std = tp_config.get("std_dev", 2.0)
            bb = bollinger_bands(close, period=period, std_dev=std)
            if condition == "price_above_middle":
                mid = float(bb.middle.iloc[current_bar_idx])
                if not pd.isna(mid) and current_price > mid:
                    return ExitSignal(should_exit=True, reason=f"take profit: price {current_price:.2f} > bb middle {mid:.2f}")
            elif condition == "price_above_upper":
                upper = float(bb.upper.iloc[current_bar_idx])
                if not pd.isna(upper) and current_price > upper:
                    return ExitSignal(should_exit=True, reason=f"take profit: price {current_price:.2f} > bb upper {upper:.2f}")

        return ExitSignal(should_exit=False)

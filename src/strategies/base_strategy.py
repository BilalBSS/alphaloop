
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import structlog

from ._analysis_handlers import ANALYSIS_HANDLERS
from ._filters import FILTER_HANDLERS
from ._signal_handlers import SIGNAL_HANDLERS

logger = structlog.get_logger(__name__)


@dataclass
class EntrySignal:
    should_enter: bool
    strength: float = 0.0  # 0.0 to 1.0
    reasons: list[str] = field(default_factory=list)
    passed_count: int = 0
    total_count: int = 0
    failed_reasons: list[str] = field(default_factory=list)


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
    # / alternative data fields
    macro_score: float | None = None
    congressional_buy_ratio: float | None = None
    analyst_consensus: float | None = None
    price_target_upside: float | None = None
    earnings_revision_momentum: float | None = None
    short_pct_float: float | None = None
    dark_pool_ratio: float | None = None
    iv_rank: float | None = None
    put_call_ratio: float | None = None
    days_to_earnings: int | None = None
    intermarket_score: float | None = None
    sector_relative_strength: float | None = None
    hurst: float | None = None


class StrategyInterface(ABC):
    @abstractmethod
    def should_enter(
        self,
        symbol: str,
        market_data: pd.DataFrame,
        analysis: AnalysisData | None = None,
        intraday_df: pd.DataFrame | None = None,
    ) -> EntrySignal:
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
        ...

    @abstractmethod
    def position_size(
        self,
        equity: float,
        price: float,
        strength: float,
    ) -> PositionSizeResult:
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
    # / loader-created strategy

    def __init__(self, config: dict[str, Any]):
        self._config = config
        self._id = config["id"]
        self._name = config["name"]
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
        return self._universe_ref

    def resolve_universe(self, available_symbols: list[str] | None = None) -> list[str]:
        symbol = self._config.get("symbol")
        if symbol:
            return [symbol]
        sector = self._config.get("sector")
        if sector:
            from src.data.symbols import get_sector_symbols
            syms = get_sector_symbols(sector)
            if syms:
                return syms
        from src.data.symbols import resolve_universe
        return resolve_universe(self._universe_ref, available_symbols)

    @property
    def requires_fundamentals(self) -> bool:
        return self._requires_fundamentals

    def _get_active_filters(self, analysis) -> dict:
        filters = dict(self._fundamental_filters)
        if (
            analysis is not None
            and getattr(analysis, "regime", None) == "bear"
            and self._bear_market_overrides
        ):
            override_ff = self._bear_market_overrides.get("fundamental_filters") or {}
            for key, value in override_ff.items():
                if value is not None:
                    filters[key] = value
        return filters

    def get_effective_bypass_consensus(self, regime: str | None = None) -> bool:
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

        if self._requires_fundamentals:
            if analysis is None:
                return EntrySignal(should_enter=False, reasons=["no fundamental data"])
            active_filters = self._get_active_filters(analysis)
            passed, fundamental_reasons = self._check_fundamentals(analysis, active_filters)
            if not passed:
                return EntrySignal(should_enter=False, reasons=fundamental_reasons)

        # / insider, etc.) have access.
        passed, strength, tech_reasons, passed_n, total_n, failed = (
            self._check_entry_technicals(market_data, intraday_df, analysis)
        )
        if not passed:
            return EntrySignal(
                should_enter=False, reasons=tech_reasons,
                passed_count=passed_n, total_count=total_n, failed_reasons=failed,
            )

        reasons = tech_reasons
        if self._requires_fundamentals:
            reasons = ["fundamentals passed", *reasons]

        return EntrySignal(
            should_enter=True, strength=strength, reasons=reasons,
            passed_count=passed_n, total_count=total_n, failed_reasons=[],
        )

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
                take_profit, market_data, entry_price, current_bar_idx,
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
            pct = min(kelly_f * strength, max_pct)
        elif method == "fixed_pct":
            pct = max_pct
        elif method == "strength_scaled":
            pct = max_pct * strength
        else:
            pct = max_pct

        if price <= 0:
            return PositionSizeResult(qty=0, pct_of_portfolio=0, method=method)

        position_value = equity * pct
        qty = int(position_value / price)  # / whole shares only
        actual_pct = (qty * price) / equity if equity > 0 else 0

        return PositionSizeResult(qty=qty, pct_of_portfolio=actual_pct, method=method)

    def _check_fundamentals(self, analysis: AnalysisData, filters=None) -> tuple[bool, list[str]]:
        filters = filters or self._fundamental_filters

        for key, threshold in filters.items():
            if key == "strict_data":
                continue
            if threshold is None:
                continue
            handler = FILTER_HANDLERS.get(key)
            if handler is None:
                continue
            passed, reason = handler(analysis, threshold, filters)
            if not passed:
                return False, [reason]

        return True, ["fundamentals passed"]

    def _check_entry_technicals(
        self, market_data: pd.DataFrame, intraday_df: pd.DataFrame | None = None,
        analysis_data: AnalysisData | None = None,
    ) -> tuple[bool, float, list[str], int, int, list[str]]:
        signals = self._entry_conditions.get("signals", [])
        operator = self._entry_conditions.get("operator", "AND")

        if not signals:
            return True, 1.0, ["no technical conditions"], 0, 0, []

        results: list[tuple[bool, float, str]] = []
        for sig in signals:
            tf = sig.get("timeframe")
            if tf and tf.lower() not in ("1d", "daily", "1day"):
                if intraday_df is not None and len(intraday_df) >= 14:
                    passed, strength, reason = self._evaluate_signal(sig, intraday_df, analysis_data)
                    results.append((passed, strength, f"[{tf}] {reason}"))
                else:
                    results.append((False, 0.0, f"{sig.get('indicator', '?')} skipped: no {tf} data"))
                continue
            passed, strength, reason = self._evaluate_signal(sig, market_data, analysis_data)
            results.append((passed, strength, reason))

        passed_count = sum(1 for r in results if r[0])
        total_count = len(results)
        failed_reasons = [r[2] for r in results if not r[0]]

        if operator == "AND":
            all_passed = all(r[0] for r in results)
            if not all_passed:
                return False, 0.0, failed_reasons, passed_count, total_count, failed_reasons
            avg_strength = sum(r[1] for r in results) / len(results)
            reasons = [r[2] for r in results]
            return True, avg_strength, reasons, passed_count, total_count, []
        else:  # OR
            any_passed = any(r[0] for r in results)
            if not any_passed:
                return False, 0.0, [r[2] for r in results], passed_count, total_count, failed_reasons
            passed_results = [r for r in results if r[0]]
            max_strength = max(r[1] for r in passed_results)
            reasons = [r[2] for r in passed_results]
            return True, max_strength, reasons, passed_count, total_count, []

    def _evaluate_signal(
        self, sig: dict[str, Any], market_data: pd.DataFrame,
        analysis_data: AnalysisData | None = None,
    ) -> tuple[bool, float, str]:
        indicator = sig.get("indicator", "")
        try:
            analysis_handler = ANALYSIS_HANDLERS.get(indicator)
            if analysis_handler is not None:
                return analysis_handler(sig, market_data, analysis_data)
            handler = SIGNAL_HANDLERS.get(indicator)
            if handler is None:
                return False, 0.0, f"unknown indicator: {indicator}"
            return handler(sig, market_data)
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
            period = int(stop_config.get("period", 14))
            multiplier = stop_config.get("multiplier", 2.0)
            high = market_data["high"]
            low = market_data["low"]
            close = market_data["close"]
            atr_val = atr_fn(high, low, close, period=period)

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
        entry_price: float,
        current_bar_idx: int,
    ) -> ExitSignal:
        close = market_data["close"]
        current_price = float(close.iloc[current_bar_idx])

        # / fixed pct branch
        pct = tp_config.get("pct")
        if pct is not None and pct > 0:
            target = entry_price * (1.0 + pct)
            if current_price >= target:
                return ExitSignal(
                    should_exit=True,
                    reason=f"take profit: price {current_price:.2f} >= {target:.2f} ({pct:.0%})",
                )
            return ExitSignal(should_exit=False)

        indicator = tp_config.get("indicator", "")
        condition = tp_config.get("condition", "")

        if indicator == "bollinger_bands":
            from src.indicators.volatility import bollinger_bands
            period = int(tp_config.get("lookback", tp_config.get("period", 20)))
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

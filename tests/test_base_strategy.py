# / tests for strategy base module — config-driven strategy evaluation

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.strategies.base_strategy import (
    AnalysisData,
    ConfigDrivenStrategy,
    EntrySignal,
    ExitSignal,
    PositionSizeResult,
)

# ---------------------------------------------------------------------------
# / helpers
# ---------------------------------------------------------------------------

def _ohlcv(n: int = 120, seed: int = 42, base: float = 100.0,
            trend: float = 0.0) -> pd.DataFrame:
    # / generate realistic ohlcv data with enough bars for indicator warmup
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n, freq="B")
    close = np.empty(n)
    close[0] = base
    for i in range(1, n):
        close[i] = close[i - 1] * (1 + trend / n + rng.normal(0, 0.015))
    high = close + rng.uniform(0.3, 1.5, n)
    low = close - rng.uniform(0.3, 1.5, n)
    open_ = close + rng.normal(0, 0.5, n)
    volume = rng.integers(500_000, 2_000_000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


def _ohlcv_dropping(n: int = 120, seed: int = 42) -> pd.DataFrame:
    # / price trending sharply down — triggers oversold indicators
    return _ohlcv(n, seed=seed, base=120.0, trend=-0.40)


def _ohlcv_rising(n: int = 120, seed: int = 42) -> pd.DataFrame:
    # / price trending sharply up — triggers overbought indicators
    return _ohlcv(n, seed=seed, base=80.0, trend=0.50)


def _ohlcv_with_volume_spike(n: int = 120, seed: int = 42) -> pd.DataFrame:
    # / normal data but last bar has extreme volume
    df = _ohlcv(n, seed=seed)
    avg_vol = df["volume"].mean()
    df.iloc[-1, df.columns.get_loc("volume")] = avg_vol * 5.0
    return df


def _base_config(**overrides) -> dict:
    cfg = {
        "id": "test_001",
        "name": "TestStrategy",
        "universe": ["AAPL", "MSFT"],
        "fundamental_filters": {},
        "entry_conditions": {"operator": "AND", "signals": []},
        "exit_conditions": {},
        "position_sizing": {"method": "fixed_pct", "max_position_pct": 0.05},
    }
    cfg.update(overrides)
    return cfg


def _strategy_001_config() -> dict:
    # / mirrors strategy_001.json — bollinger + rsi + volume with fundamentals
    return {
        "id": "strategy_001",
        "name": "Bollinger_PE_Oversold",
        "universe": ["AAPL", "MSFT", "GOOG"],
        "fundamental_filters": {
            "pe_ratio_max": 40,
            "pe_vs_sector": "below_average",
            "revenue_growth_min": 0.01,
            "fcf_margin_min": 0.10,
            "debt_to_equity_max": 2.0,
            "dcf_upside_min": 0.05,
        },
        "entry_conditions": {
            "operator": "AND",
            "signals": [
                {"indicator": "bollinger_bands", "condition": "price_below_lower",
                 "lookback": 20, "std_dev": 2.0},
                {"indicator": "rsi", "condition": "below", "threshold": 35, "period": 14},
                {"indicator": "volume", "condition": "above_average",
                 "multiplier": 1.3, "period": 20},
            ],
        },
        "exit_conditions": {
            "take_profit": {"indicator": "bollinger_bands", "condition": "price_above_middle"},
            "stop_loss": {"type": "atr_trailing", "multiplier": 2.0, "period": 14},
            "time_exit": {"max_holding_days": 30},
        },
        "position_sizing": {
            "method": "kelly_fraction",
            "max_position_pct": 0.08,
            "kelly_fraction": 0.25,
        },
    }


def _strategy_002_config() -> dict:
    # / mirrors strategy_002.json — macd momentum, no fundamentals
    return {
        "id": "strategy_002",
        "name": "MACD_Momentum_Breakout",
        "universe": ["AAPL", "MSFT", "GOOG"],
        "entry_conditions": {
            "operator": "AND",
            "signals": [
                {"indicator": "macd", "condition": "crossover_bullish"},
                {"indicator": "volume", "condition": "above_average",
                 "multiplier": 1.2, "period": 20},
                {"indicator": "adx", "condition": "above", "threshold": 25, "period": 14},
            ],
        },
        "exit_conditions": {
            "stop_loss": {"type": "atr_trailing", "multiplier": 1.5, "period": 14},
            "take_profit": {"indicator": "bollinger_bands",
                            "condition": "price_above_upper", "period": 20, "std_dev": 2.0},
            "time_exit": {"max_holding_days": 20},
        },
        "position_sizing": {
            "method": "strength_scaled",
            "max_position_pct": 0.04,
        },
    }


def _passing_analysis() -> AnalysisData:
    # / analysis that passes all strategy_001 fundamental filters
    return AnalysisData(
        pe_ratio=20.0,
        sector_pe_avg=25.0,
        revenue_growth=0.10,
        fcf_margin=0.20,
        debt_to_equity=0.8,
        dcf_upside=0.15,
    )


def _failing_analysis(**overrides) -> AnalysisData:
    # / start from passing, then override to fail a specific filter
    a = _passing_analysis()
    for k, v in overrides.items():
        setattr(a, k, v)
    return a


# ---------------------------------------------------------------------------
# / initialization
# ---------------------------------------------------------------------------

class TestInitialization:
    def test_properties_from_config(self):
        cfg = _strategy_001_config()
        s = ConfigDrivenStrategy(cfg)
        assert s.strategy_id == "strategy_001"
        assert s.name == "Bollinger_PE_Oversold"
        assert s.config is cfg

    def test_universe_ref(self):
        s = ConfigDrivenStrategy(_strategy_001_config())
        assert isinstance(s.universe_ref, str)
        # / legacy list configs get stored as the raw value
        assert s.universe_ref is not None

    def test_requires_fundamentals_when_filters_present(self):
        s = ConfigDrivenStrategy(_strategy_001_config())
        assert s.requires_fundamentals is True

    def test_no_fundamentals_when_filters_absent(self):
        s = ConfigDrivenStrategy(_strategy_002_config())
        assert s.requires_fundamentals is False

    def test_empty_universe_defaults(self):
        cfg = _base_config()
        del cfg["universe"]
        s = ConfigDrivenStrategy(cfg)
        assert s.universe_ref == "all"

    def test_missing_optional_sections(self):
        # / strategy with only id/name should still init
        cfg = {"id": "minimal", "name": "Minimal"}
        s = ConfigDrivenStrategy(cfg)
        assert s.strategy_id == "minimal"
        assert s.requires_fundamentals is False


# ---------------------------------------------------------------------------
# / fundamental filters
# ---------------------------------------------------------------------------

class TestFundamentalFilters:
    def setup_method(self):
        self.strategy = ConfigDrivenStrategy(_strategy_001_config())
        self.data = _ohlcv()

    def test_passes_all_filters(self):
        result = self.strategy.should_enter("AAPL", self.data, _passing_analysis())
        # / may fail on technicals, but should not fail on fundamentals
        if not result.should_enter:
            assert "fundamentals" not in " ".join(result.reasons).lower() or \
                all("pe" not in r and "revenue" not in r and "fcf" not in r
                    and "d/e" not in r and "dcf" not in r
                    for r in result.reasons)

    def test_rejects_no_analysis_data(self):
        result = self.strategy.should_enter("AAPL", self.data, analysis=None)
        assert result.should_enter is False
        assert "no fundamental data" in result.reasons

    def test_pe_ratio_max_exceeded(self):
        analysis = _failing_analysis(pe_ratio=50.0)
        result = self.strategy.should_enter("AAPL", self.data, analysis)
        assert result.should_enter is False
        assert any("pe" in r.lower() for r in result.reasons)

    def test_pe_ratio_at_boundary(self):
        # / pe exactly at max should pass
        analysis = _failing_analysis(pe_ratio=40.0)
        result = self.strategy.should_enter("AAPL", self.data, analysis)
        # / should not fail on pe filter
        assert not any("pe 40.0 > max" in r for r in result.reasons)

    def test_pe_vs_sector_above_average(self):
        analysis = _failing_analysis(pe_ratio=30.0, sector_pe_avg=25.0)
        result = self.strategy.should_enter("AAPL", self.data, analysis)
        assert result.should_enter is False
        assert any("sector" in r for r in result.reasons)

    def test_pe_vs_sector_below_average_passes(self):
        analysis = _failing_analysis(pe_ratio=20.0, sector_pe_avg=25.0)
        result = self.strategy.should_enter("AAPL", self.data, analysis)
        assert not any("sector" in r for r in result.reasons)

    def test_revenue_growth_too_low(self):
        analysis = _failing_analysis(revenue_growth=0.005)
        result = self.strategy.should_enter("AAPL", self.data, analysis)
        assert result.should_enter is False
        assert any("revenue" in r for r in result.reasons)

    def test_fcf_margin_too_low(self):
        analysis = _failing_analysis(fcf_margin=0.05)
        result = self.strategy.should_enter("AAPL", self.data, analysis)
        assert result.should_enter is False
        assert any("fcf" in r for r in result.reasons)

    def test_debt_to_equity_too_high(self):
        analysis = _failing_analysis(debt_to_equity=3.0)
        result = self.strategy.should_enter("AAPL", self.data, analysis)
        assert result.should_enter is False
        assert any("d/e" in r for r in result.reasons)

    def test_dcf_upside_too_low(self):
        analysis = _failing_analysis(dcf_upside=0.02)
        result = self.strategy.should_enter("AAPL", self.data, analysis)
        assert result.should_enter is False
        assert any("dcf" in r for r in result.reasons)

    def test_none_values_reject_filter(self):
        # / if analysis field is none and filter is configured, reject (data unavailable)
        analysis = AnalysisData(
            pe_ratio=None, sector_pe_avg=None,
            revenue_growth=None, fcf_margin=None,
            debt_to_equity=None, dcf_upside=None,
        )
        result = self.strategy.should_enter("AAPL", self.data, analysis)
        # / should fail on fundamentals — none values mean data unavailable
        assert result.should_enter is False
        assert any("unavailable" in r for r in result.reasons)

    def test_no_fundamental_strategy_skips_filters(self):
        s = ConfigDrivenStrategy(_strategy_002_config())
        # / should not require analysis at all
        result = s.should_enter("AAPL", self.data, analysis=None)
        assert "no fundamental data" not in result.reasons


# ---------------------------------------------------------------------------
# / entry signals — individual indicators
# ---------------------------------------------------------------------------

class TestEntryBollingerBands:
    def test_price_below_lower_on_downtrend(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "bollinger_bands", "condition": "price_below_lower",
                         "lookback": 20, "std_dev": 2.0}],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_dropping(150, seed=42)
        result = s.should_enter("AAPL", data)
        # / on a strong downtrend the last price should be below lower band
        assert "bb:" in result.reasons[0]

    def test_price_above_upper_on_uptrend(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "bollinger_bands", "condition": "price_above_upper",
                         "lookback": 20, "std_dev": 2.0}],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150, seed=42)
        result = s.should_enter("AAPL", data)
        assert "bb:" in result.reasons[0]

    def test_price_above_middle(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "bollinger_bands", "condition": "price_above_middle",
                         "lookback": 20, "std_dev": 2.0}],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150, seed=42)
        result = s.should_enter("AAPL", data)
        # / on an uptrend, close should be above the sma middle
        assert result.should_enter is True
        assert result.strength == pytest.approx(0.5)


class TestEntryRSI:
    def test_rsi_below_threshold_on_downtrend(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "rsi", "condition": "below",
                         "threshold": 40, "period": 14}],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_dropping(150, seed=42)
        result = s.should_enter("AAPL", data)
        assert result.should_enter is True
        assert result.strength > 0
        assert "rsi=" in result.reasons[0]

    def test_rsi_above_threshold_on_uptrend(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "rsi", "condition": "above",
                         "threshold": 55, "period": 14}],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150, seed=42)
        result = s.should_enter("AAPL", data)
        assert result.should_enter is True
        assert result.strength > 0

    def test_rsi_strength_bounded_0_to_1(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "rsi", "condition": "below",
                         "threshold": 80, "period": 14}],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(150)
        result = s.should_enter("AAPL", data)
        if result.should_enter:
            assert 0.0 <= result.strength <= 1.0


class TestEntryVolume:
    def test_volume_above_average_with_spike(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "volume", "condition": "above_average",
                         "multiplier": 1.5, "period": 20}],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_with_volume_spike(120)
        result = s.should_enter("AAPL", data)
        assert result.should_enter is True
        assert "vol=" in result.reasons[0]

    def test_volume_below_average_fails(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "volume", "condition": "above_average",
                         "multiplier": 3.0, "period": 20}],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(120)  # / normal volume, no spike
        result = s.should_enter("AAPL", data)
        assert result.should_enter is False


class TestEntryMACD:
    def _macd_crossover_data(self, seed: int = 42) -> pd.DataFrame:
        # / build data where macd histogram crosses from negative to positive
        # / downtrend then uptrend forces a crossover in the histogram
        rng = np.random.default_rng(seed)
        n = 150
        dates = pd.bdate_range("2024-01-02", periods=n, freq="B")
        close = np.empty(n)
        close[0] = 100.0
        # / first half: drift down
        for i in range(1, n // 2):
            close[i] = close[i - 1] * (1 + rng.normal(-0.003, 0.01))
        # / second half: drift up sharply to create crossover
        for i in range(n // 2, n):
            close[i] = close[i - 1] * (1 + rng.normal(0.006, 0.01))
        high = close + rng.uniform(0.3, 1.2, n)
        low = close - rng.uniform(0.3, 1.2, n)
        open_ = close + rng.normal(0, 0.3, n)
        volume = rng.integers(500_000, 2_000_000, n).astype(float)
        return pd.DataFrame(
            {"open": open_, "high": high, "low": low,
             "close": close, "volume": volume},
            index=dates,
        )

    def test_macd_crossover_bullish(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "macd", "condition": "crossover_bullish"}],
        })
        s = ConfigDrivenStrategy(cfg)
        data = self._macd_crossover_data()
        result = s.should_enter("AAPL", data)
        # / crossover_bullish now uses an N-bar lookback window (default 3) so
        # / the reason string references "crossover_bullish in last N bars"
        # / rather than the old single-bar histogram delta.
        assert "crossover_bullish" in result.reasons[0]

    def test_macd_positive(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "macd", "condition": "positive"}],
        })
        s = ConfigDrivenStrategy(cfg)
        # / need a very strong uptrend so histogram stays positive at the end
        data = _ohlcv_rising(150, seed=7)
        result = s.should_enter("AAPL", data)
        # / strong uptrend should have positive histogram
        assert result.should_enter is True
        assert result.strength == pytest.approx(0.5)

    def test_macd_crossover_bearish(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "macd", "condition": "crossover_bearish"}],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(150)
        result = s.should_enter("AAPL", data)
        assert "crossover_bearish" in result.reasons[0]


class TestEntrySMA:
    def test_price_above_sma_on_uptrend(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "sma", "condition": "price_above", "period": 20}],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(120)
        result = s.should_enter("AAPL", data)
        assert result.should_enter is True
        assert "sma" in result.reasons[0]

    def test_price_below_sma_on_downtrend(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "sma", "condition": "price_below", "period": 20}],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_dropping(120)
        result = s.should_enter("AAPL", data)
        assert result.should_enter is True
        assert "sma" in result.reasons[0]


class TestEntryADX:
    def test_adx_above_threshold(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "adx", "condition": "above",
                         "threshold": 15, "period": 14}],
        })
        s = ConfigDrivenStrategy(cfg)
        # / strong trending data should have elevated adx
        data = _ohlcv_rising(150, seed=42)
        result = s.should_enter("AAPL", data)
        assert "adx=" in result.reasons[0]

    def test_adx_below_threshold(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "adx", "condition": "below",
                         "threshold": 80, "period": 14}],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(150)
        result = s.should_enter("AAPL", data)
        # / adx almost never hits 80, so below should pass
        assert result.should_enter is True
        assert result.strength == pytest.approx(0.5)


class TestEntryStochastic:
    def test_stochastic_below_on_downtrend(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "stochastic", "condition": "below",
                         "threshold": 30, "period": 14}],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_dropping(150)
        result = s.should_enter("AAPL", data)
        assert "stoch" in result.reasons[0]

    def test_stochastic_above_on_uptrend(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "stochastic", "condition": "above",
                         "threshold": 70, "period": 14}],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150)
        result = s.should_enter("AAPL", data)
        assert "stoch" in result.reasons[0]


class TestEntryUnknownIndicator:
    def test_unknown_indicator_fails(self):
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "magic_indicator", "condition": "magic"}],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(120)
        result = s.should_enter("AAPL", data)
        assert result.should_enter is False
        assert any("unknown" in r for r in result.reasons)


# ---------------------------------------------------------------------------
# / entry — operator logic (AND / OR)
# ---------------------------------------------------------------------------

class TestEntryOperators:
    def test_and_operator_all_pass(self):
        # / sma price_above on uptrend + low adx threshold — both should pass
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [
                {"indicator": "sma", "condition": "price_above", "period": 20},
                {"indicator": "adx", "condition": "below", "threshold": 90, "period": 14},
            ],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150)
        result = s.should_enter("AAPL", data)
        assert result.should_enter is True
        assert len(result.reasons) == 2

    def test_and_operator_one_fails(self):
        # / sma price_above on downtrend should fail
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [
                {"indicator": "sma", "condition": "price_above", "period": 20},
                {"indicator": "adx", "condition": "below", "threshold": 90, "period": 14},
            ],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_dropping(150)
        result = s.should_enter("AAPL", data)
        assert result.should_enter is False

    def test_or_operator_one_passes(self):
        # / one signal passes, one fails — should still enter with OR
        cfg = _base_config(entry_conditions={
            "operator": "OR",
            "signals": [
                {"indicator": "sma", "condition": "price_above", "period": 20},
                {"indicator": "adx", "condition": "below", "threshold": 90, "period": 14},
            ],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_dropping(150)
        result = s.should_enter("AAPL", data)
        # / adx below 90 should pass even though sma fails
        assert result.should_enter is True

    def test_or_operator_none_pass(self):
        # / both require impossible conditions
        cfg = _base_config(entry_conditions={
            "operator": "OR",
            "signals": [
                {"indicator": "rsi", "condition": "below", "threshold": 1, "period": 14},
                {"indicator": "adx", "condition": "above", "threshold": 99, "period": 14},
            ],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(150)
        result = s.should_enter("AAPL", data)
        assert result.should_enter is False

    def test_no_signals_passes(self):
        cfg = _base_config(entry_conditions={"operator": "AND", "signals": []})
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(120)
        result = s.should_enter("AAPL", data)
        assert result.should_enter is True
        assert result.strength == pytest.approx(1.0)

    def test_and_strength_is_average(self):
        # / two signals that both pass — strength should be average
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [
                {"indicator": "sma", "condition": "price_above", "period": 20},
                {"indicator": "adx", "condition": "below", "threshold": 90, "period": 14},
            ],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150)
        result = s.should_enter("AAPL", data)
        if result.should_enter:
            # / both return 0.5 so average should be 0.5
            assert result.strength == pytest.approx(0.5)

    def test_or_strength_is_max(self):
        cfg = _base_config(entry_conditions={
            "operator": "OR",
            "signals": [
                {"indicator": "sma", "condition": "price_above", "period": 20},
                {"indicator": "adx", "condition": "below", "threshold": 90, "period": 14},
            ],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150)
        result = s.should_enter("AAPL", data)
        if result.should_enter:
            assert result.strength == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# / exit signals
# ---------------------------------------------------------------------------

class TestExitTimeExit:
    def setup_method(self):
        cfg = _base_config(exit_conditions={
            "time_exit": {"max_holding_days": 10},
        })
        self.strategy = ConfigDrivenStrategy(cfg)
        self.data = _ohlcv(120)

    def test_exits_after_max_days(self):
        entry_date = self.data.index[0]
        # / bar 50 is well past 10 business days
        result = self.strategy.should_exit("AAPL", self.data, 100.0, entry_date, 50)
        assert result.should_exit is True
        assert "time exit" in result.reason

    def test_no_exit_before_max_days(self):
        entry_date = self.data.index[0]
        # / bar 5 is within 10 days
        result = self.strategy.should_exit("AAPL", self.data, 100.0, entry_date, 5)
        assert result.should_exit is False

    def test_exits_exactly_at_max_days(self):
        entry_date = self.data.index[0]
        # / find bar index where days_held >= 10
        for i in range(len(self.data)):
            current_date = self.data.index[i]
            days = (current_date - entry_date).days
            if days >= 10:
                result = self.strategy.should_exit("AAPL", self.data, 100.0, entry_date, i)
                assert result.should_exit is True
                break


class TestExitStopLossFixed:
    def setup_method(self):
        cfg = _base_config(exit_conditions={
            "stop_loss": {"type": "fixed_pct", "pct": 0.05},
        })
        self.strategy = ConfigDrivenStrategy(cfg)

    def test_stop_loss_triggered(self):
        # / entry at 100, stop at 95 — create data that drops below 95
        data = _ohlcv_dropping(120, seed=42)
        entry_price = float(data["close"].iloc[0])
        stop_price = entry_price * 0.95
        # / find a bar where close <= stop_price
        for i in range(len(data)):
            if float(data["close"].iloc[i]) <= stop_price:
                entry_date = data.index[0]
                result = self.strategy.should_exit(
                    "AAPL", data, entry_price, entry_date, i,
                )
                assert result.should_exit is True
                assert "stop loss" in result.reason
                break

    def test_stop_loss_not_triggered(self):
        data = _ohlcv_rising(120)
        entry_price = float(data["close"].iloc[0])
        entry_date = data.index[0]
        # / on an uptrend, price shouldn't drop 5%
        result = self.strategy.should_exit("AAPL", data, entry_price, entry_date, 50)
        assert result.should_exit is False


class TestExitStopLossATRTrailing:
    def setup_method(self):
        cfg = _base_config(exit_conditions={
            "stop_loss": {"type": "atr_trailing", "multiplier": 2.0, "period": 14},
        })
        self.strategy = ConfigDrivenStrategy(cfg)

    def test_atr_trailing_stop_on_drop(self):
        # / strong downtrend from high should trigger trailing stop
        rng = np.random.default_rng(99)
        n = 120
        dates = pd.bdate_range("2024-01-02", periods=n, freq="B")
        close = np.empty(n)
        close[0] = 100.0
        # / first 40 bars: rise to peak
        for i in range(1, 40):
            close[i] = close[i - 1] * (1 + rng.normal(0.005, 0.005))
        # / then crash hard
        for i in range(40, n):
            close[i] = close[i - 1] * (1 + rng.normal(-0.02, 0.008))
        high = close + rng.uniform(0.3, 1.0, n)
        low = close - rng.uniform(0.3, 1.0, n)
        open_ = close + rng.normal(0, 0.3, n)
        volume = rng.integers(500_000, 2_000_000, n).astype(float)
        data = pd.DataFrame(
            {"open": open_, "high": high, "low": low,
             "close": close, "volume": volume},
            index=dates,
        )
        entry_price = float(data["close"].iloc[20])
        entry_date = data.index[20]
        # / check a late bar when price has dropped significantly
        result = self.strategy.should_exit("AAPL", data, entry_price, entry_date, 90)
        assert result.should_exit is True
        assert "atr trailing stop" in result.reason

    def test_atr_trailing_no_exit_in_uptrend(self):
        data = _ohlcv_rising(120)
        entry_price = float(data["close"].iloc[20])
        entry_date = data.index[20]
        result = self.strategy.should_exit("AAPL", data, entry_price, entry_date, 80)
        assert result.should_exit is False


class TestExitTakeProfitBollinger:
    def test_take_profit_above_middle(self):
        cfg = _base_config(exit_conditions={
            "take_profit": {"indicator": "bollinger_bands",
                            "condition": "price_above_middle", "period": 20, "std_dev": 2.0},
        })
        s = ConfigDrivenStrategy(cfg)
        # / use 150 bars and check near the end where sma is well-defined
        data = _ohlcv_rising(150)
        entry_date = data.index[0]
        # / on strong uptrend, last bar's close should exceed sma(20) middle
        # / check multiple bars near the end to find one that triggers
        triggered = False
        for idx in range(130, 149):
            result = s.should_exit("AAPL", data, 80.0, entry_date, idx)
            if result.should_exit:
                assert "take profit" in result.reason
                triggered = True
                break
        assert triggered, "take profit should trigger on strong uptrend"

    def test_take_profit_above_upper(self):
        cfg = _base_config(exit_conditions={
            "take_profit": {"indicator": "bollinger_bands",
                            "condition": "price_above_upper", "period": 20, "std_dev": 2.0},
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150, seed=42)
        entry_date = data.index[0]
        # / check near end on a strong uptrend
        result = s.should_exit("AAPL", data, 80.0, entry_date, 140)
        assert "bb" in result.reason.lower() or result.should_exit is False

    def test_take_profit_not_triggered_on_downtrend(self):
        cfg = _base_config(exit_conditions={
            "take_profit": {"indicator": "bollinger_bands",
                            "condition": "price_above_middle", "period": 20, "std_dev": 2.0},
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_dropping(120)
        entry_date = data.index[0]
        result = s.should_exit("AAPL", data, 120.0, entry_date, 100)
        assert result.should_exit is False


class TestExitBoundary:
    def test_current_bar_idx_out_of_range(self):
        cfg = _base_config(exit_conditions={"time_exit": {"max_holding_days": 5}})
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(50)
        entry_date = data.index[0]
        result = s.should_exit("AAPL", data, 100.0, entry_date, 999)
        assert result.should_exit is False

    def test_no_exit_conditions(self):
        cfg = _base_config(exit_conditions={})
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(50)
        entry_date = data.index[0]
        result = s.should_exit("AAPL", data, 100.0, entry_date, 30)
        assert result.should_exit is False

    def test_exit_priority_time_before_stop_loss(self):
        # / time exit triggers first when both conditions are met
        cfg = _base_config(exit_conditions={
            "time_exit": {"max_holding_days": 5},
            "stop_loss": {"type": "fixed_pct", "pct": 0.05},
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_dropping(120)
        entry_date = data.index[0]
        entry_price = float(data["close"].iloc[0])
        # / check at bar 50 — both should fire but time_exit is checked first
        result = s.should_exit("AAPL", data, entry_price, entry_date, 50)
        assert result.should_exit is True
        assert "time exit" in result.reason


# ---------------------------------------------------------------------------
# / position sizing
# ---------------------------------------------------------------------------

class TestPositionSizingKellyFraction:
    def setup_method(self):
        cfg = _base_config(position_sizing={
            "method": "kelly_fraction",
            "max_position_pct": 0.08,
            "kelly_fraction": 0.25,
        })
        self.strategy = ConfigDrivenStrategy(cfg)

    def test_kelly_basic(self):
        result = self.strategy.position_size(equity=100_000, price=150.0, strength=0.8)
        assert result.method == "kelly_fraction"
        # / kelly_f * strength = 0.25 * 0.8 = 0.20 — capped at 0.08
        expected_pct = 0.08
        expected_value = 100_000 * expected_pct
        expected_qty = int(expected_value / 150.0)
        assert result.qty == expected_qty

    def test_kelly_low_strength(self):
        result = self.strategy.position_size(equity=100_000, price=150.0, strength=0.2)
        # / kelly_f * strength = 0.25 * 0.2 = 0.05 — below cap
        expected_pct = 0.05
        expected_value = 100_000 * expected_pct
        expected_qty = int(expected_value / 150.0)
        assert result.qty == expected_qty

    def test_kelly_zero_strength(self):
        result = self.strategy.position_size(equity=100_000, price=150.0, strength=0.0)
        assert result.qty == 0

    def test_kelly_capped_at_max(self):
        result = self.strategy.position_size(equity=100_000, price=50.0, strength=1.0)
        # / 0.25 * 1.0 = 0.25 > max 0.08 — should be capped
        max_value = 100_000 * 0.08
        assert result.qty == int(max_value / 50.0)


class TestPositionSizingFixedPct:
    def setup_method(self):
        cfg = _base_config(position_sizing={
            "method": "fixed_pct",
            "max_position_pct": 0.05,
        })
        self.strategy = ConfigDrivenStrategy(cfg)

    def test_fixed_pct_ignores_strength(self):
        r1 = self.strategy.position_size(equity=100_000, price=100.0, strength=0.3)
        r2 = self.strategy.position_size(equity=100_000, price=100.0, strength=0.9)
        assert r1.qty == r2.qty
        assert r1.method == "fixed_pct"

    def test_fixed_pct_basic(self):
        result = self.strategy.position_size(equity=100_000, price=200.0, strength=0.5)
        expected_value = 100_000 * 0.05
        expected_qty = int(expected_value / 200.0)
        assert result.qty == expected_qty

    def test_whole_shares_only(self):
        result = self.strategy.position_size(equity=10_000, price=333.33, strength=0.5)
        assert isinstance(result.qty, int)


class TestPositionSizingStrengthScaled:
    def setup_method(self):
        cfg = _base_config(position_sizing={
            "method": "strength_scaled",
            "max_position_pct": 0.04,
        })
        self.strategy = ConfigDrivenStrategy(cfg)

    def test_scales_with_strength(self):
        r_low = self.strategy.position_size(equity=100_000, price=100.0, strength=0.25)
        r_high = self.strategy.position_size(equity=100_000, price=100.0, strength=1.0)
        assert r_high.qty > r_low.qty
        assert r_low.method == "strength_scaled"

    def test_full_strength(self):
        result = self.strategy.position_size(equity=100_000, price=100.0, strength=1.0)
        expected_value = 100_000 * 0.04
        expected_qty = int(expected_value / 100.0)
        assert result.qty == expected_qty

    def test_zero_strength_zero_qty(self):
        result = self.strategy.position_size(equity=100_000, price=100.0, strength=0.0)
        assert result.qty == 0

    def test_half_strength(self):
        result = self.strategy.position_size(equity=100_000, price=100.0, strength=0.5)
        # / 0.04 * 0.5 = 0.02 -> 2000 / 100 = 20 shares
        assert result.qty == 20


class TestPositionSizingUnknownMethod:
    def test_unknown_method_defaults_to_max_pct(self):
        cfg = _base_config(position_sizing={
            "method": "some_future_method",
            "max_position_pct": 0.06,
        })
        s = ConfigDrivenStrategy(cfg)
        result = s.position_size(equity=100_000, price=100.0, strength=0.5)
        expected_qty = int(100_000 * 0.06 / 100.0)
        assert result.qty == expected_qty


class TestPositionSizingDefaultConfig:
    def test_defaults_when_no_sizing_config(self):
        cfg = {"id": "min", "name": "Min"}
        s = ConfigDrivenStrategy(cfg)
        result = s.position_size(equity=100_000, price=100.0, strength=0.5)
        # / default: fixed_pct, max 0.08
        assert result.method == "fixed_pct"
        assert result.qty == int(100_000 * 0.08 / 100.0)


# ---------------------------------------------------------------------------
# / edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_insufficient_data_returns_no_entry(self):
        s = ConfigDrivenStrategy(_base_config())
        tiny = _ohlcv(1)
        result = s.should_enter("AAPL", tiny)
        assert result.should_enter is False
        assert "insufficient data" in result.reasons

    def test_empty_dataframe_returns_no_entry(self):
        s = ConfigDrivenStrategy(_base_config())
        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        result = s.should_enter("AAPL", empty)
        assert result.should_enter is False

    def test_zero_price_position_size(self):
        s = ConfigDrivenStrategy(_base_config())
        result = s.position_size(equity=100_000, price=0.0, strength=0.5)
        assert result.qty == 0
        assert result.pct_of_portfolio == 0

    def test_negative_price_position_size(self):
        s = ConfigDrivenStrategy(_base_config())
        result = s.position_size(equity=100_000, price=-10.0, strength=0.5)
        assert result.qty == 0

    def test_zero_equity_position_size(self):
        s = ConfigDrivenStrategy(_base_config())
        result = s.position_size(equity=0, price=100.0, strength=0.5)
        assert result.qty == 0
        assert result.pct_of_portfolio == 0

    def test_actual_pct_accounts_for_rounding(self):
        s = ConfigDrivenStrategy(_base_config(position_sizing={
            "method": "fixed_pct", "max_position_pct": 0.05,
        }))
        result = s.position_size(equity=10_000, price=333.0, strength=0.5)
        # / actual_pct should reflect what was actually bought (whole shares)
        expected = (result.qty * 333.0) / 10_000
        assert result.pct_of_portfolio == pytest.approx(expected)

    def test_entry_signal_dataclass_defaults(self):
        sig = EntrySignal(should_enter=False)
        assert sig.strength == 0.0
        assert sig.reasons == []

    def test_exit_signal_dataclass_defaults(self):
        sig = ExitSignal(should_exit=False)
        assert sig.reason == ""

    def test_position_size_result_fields(self):
        r = PositionSizeResult(qty=10, pct_of_portfolio=0.05, method="kelly_fraction")
        assert r.qty == 10
        assert r.pct_of_portfolio == 0.05
        assert r.method == "kelly_fraction"

    def test_analysis_data_defaults(self):
        a = AnalysisData()
        assert a.pe_ratio is None
        assert a.consecutive_beats == 0
        assert a.fundamental_score is None


# ---------------------------------------------------------------------------
# / integration — full strategy_001 and strategy_002 configs
# ---------------------------------------------------------------------------

class TestIntegrationStrategy001:
    def test_rejects_without_fundamentals(self):
        s = ConfigDrivenStrategy(_strategy_001_config())
        data = _ohlcv_dropping(150)
        result = s.should_enter("AAPL", data, analysis=None)
        assert result.should_enter is False

    def test_rejects_bad_fundamentals(self):
        s = ConfigDrivenStrategy(_strategy_001_config())
        data = _ohlcv_dropping(150)
        analysis = _failing_analysis(pe_ratio=60.0)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False

    def test_exit_evaluates_all_conditions(self):
        s = ConfigDrivenStrategy(_strategy_001_config())
        data = _ohlcv(120)
        entry_date = data.index[0]
        # / very late bar to trigger time exit (30 days)
        result = s.should_exit("AAPL", data, 100.0, entry_date, 80)
        assert result.should_exit is True

    def test_kelly_position_sizing(self):
        s = ConfigDrivenStrategy(_strategy_001_config())
        result = s.position_size(equity=50_000, price=175.0, strength=0.6)
        assert result.method == "kelly_fraction"
        # / 0.25 * 0.6 = 0.15 -> capped at 0.08
        assert result.qty == int(50_000 * 0.08 / 175.0)


class TestIntegrationStrategy002:
    def test_no_fundamentals_required(self):
        s = ConfigDrivenStrategy(_strategy_002_config())
        assert s.requires_fundamentals is False

    def test_can_enter_without_analysis(self):
        s = ConfigDrivenStrategy(_strategy_002_config())
        data = _ohlcv(150)
        result = s.should_enter("AAPL", data)
        # / should evaluate technicals without needing analysis
        assert "no fundamental data" not in result.reasons

    def test_strength_scaled_sizing(self):
        s = ConfigDrivenStrategy(_strategy_002_config())
        result = s.position_size(equity=100_000, price=200.0, strength=0.7)
        assert result.method == "strength_scaled"
        # / 0.04 * 0.7 = 0.028 -> 2800 / 200 = 14, but float truncation gives 13
        pct = 0.04 * 0.7
        expected_qty = int(100_000 * pct / 200.0)
        assert result.qty == expected_qty

    def test_exit_time_limit(self):
        s = ConfigDrivenStrategy(_strategy_002_config())
        data = _ohlcv(120)
        entry_date = data.index[0]
        result = s.should_exit("AAPL", data, 100.0, entry_date, 80)
        assert result.should_exit is True
        assert "time exit" in result.reason


# ---------------------------------------------------------------------------
# / deep failure-pinpointing tests — entry operators
# ---------------------------------------------------------------------------

class TestEntryOperatorsDeep:
    def test_and_with_three_signals_one_fails(self):
        # / and operator: if even one signal fails, overall should fail
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [
                {"indicator": "sma", "condition": "price_above", "period": 20},
                {"indicator": "adx", "condition": "below", "threshold": 90, "period": 14},
                # / rsi below 1 is impossible — this signal will fail
                {"indicator": "rsi", "condition": "below", "threshold": 1, "period": 14},
            ],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150)
        result = s.should_enter("AAPL", data)
        assert result.should_enter is False
        assert result.strength == 0.0

    def test_or_strength_uses_max_of_passing(self):
        # / or operator: strength should be max of passing signals
        # / sma price_above returns 0.5 when it passes, adx below returns 0.5
        # / both pass on rising data — max of passing should be 0.5
        cfg = _base_config(entry_conditions={
            "operator": "OR",
            "signals": [
                {"indicator": "sma", "condition": "price_above", "period": 20},
                {"indicator": "adx", "condition": "below", "threshold": 90, "period": 14},
            ],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150)
        result = s.should_enter("AAPL", data)
        assert result.should_enter is True
        # / both signals return 0.5, so max should be 0.5
        assert result.strength == pytest.approx(0.5)

    def test_empty_signals_returns_true_with_strength_1(self):
        # / no signals means auto-pass with strength 1.0
        cfg = _base_config(entry_conditions={"operator": "AND", "signals": []})
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(120)
        result = s.should_enter("AAPL", data)
        assert result.should_enter is True
        assert result.strength == pytest.approx(1.0)
        assert "no technical conditions" in result.reasons


# ---------------------------------------------------------------------------
# / deep failure-pinpointing tests — position sizing
# ---------------------------------------------------------------------------

class TestPositionSizingDeep:
    def test_kelly_with_strength_zero_gives_zero_qty(self):
        cfg = _base_config(position_sizing={
            "method": "kelly_fraction",
            "max_position_pct": 0.08,
            "kelly_fraction": 0.25,
        })
        s = ConfigDrivenStrategy(cfg)
        result = s.position_size(equity=100_000, price=150.0, strength=0.0)
        # / kelly_f * 0 = 0, min(0, 0.08) = 0 => value = 0 => qty = 0
        assert result.qty == 0
        assert result.pct_of_portfolio == 0

    def test_kelly_exact_formula(self):
        # / kelly_fraction * strength * equity / price, capped at max_pct
        cfg = _base_config(position_sizing={
            "method": "kelly_fraction",
            "max_position_pct": 0.08,
            "kelly_fraction": 0.25,
        })
        s = ConfigDrivenStrategy(cfg)
        # / 0.25 * 0.2 = 0.05, below cap of 0.08
        result = s.position_size(equity=100_000, price=200.0, strength=0.2)
        pct = min(0.25 * 0.2, 0.08)  # = 0.05
        expected_value = 100_000 * pct  # = 5000
        expected_qty = int(expected_value / 200.0)  # = 25
        assert pct == pytest.approx(0.05)
        assert result.qty == expected_qty
        assert result.method == "kelly_fraction"

        # / now with strength that exceeds cap: 0.25 * 0.5 = 0.125 > 0.08
        result2 = s.position_size(equity=100_000, price=200.0, strength=0.5)
        pct2 = min(0.25 * 0.5, 0.08)  # = 0.08 (capped)
        expected_qty2 = int(100_000 * pct2 / 200.0)  # = 40
        assert result2.qty == expected_qty2

    def test_price_zero_returns_zero_qty(self):
        cfg = _base_config(position_sizing={
            "method": "kelly_fraction",
            "max_position_pct": 0.08,
            "kelly_fraction": 0.25,
        })
        s = ConfigDrivenStrategy(cfg)
        result = s.position_size(equity=100_000, price=0.0, strength=0.8)
        assert result.qty == 0
        assert result.pct_of_portfolio == 0
        assert result.method == "kelly_fraction"

    def test_very_small_price_large_qty(self):
        # / penny stock scenario: small price should yield large share count
        cfg = _base_config(position_sizing={
            "method": "fixed_pct",
            "max_position_pct": 0.05,
        })
        s = ConfigDrivenStrategy(cfg)
        result = s.position_size(equity=100_000, price=0.10, strength=0.5)
        # / 100_000 * 0.05 / 0.10 = 50_000 shares
        assert result.qty == int(100_000 * 0.05 / 0.10)
        assert result.qty == 50_000


# ---------------------------------------------------------------------------
# / deep failure-pinpointing tests — exit stop loss fixed
# ---------------------------------------------------------------------------

class TestExitStopLossFixedDeep:
    def test_exact_stop_price(self):
        # / verify stop_price = entry_price * (1 - pct)
        cfg = _base_config(exit_conditions={
            "stop_loss": {"type": "fixed_pct", "pct": 0.05},
        })
        s = ConfigDrivenStrategy(cfg)
        entry_price = 100.0
        expected_stop = entry_price * (1 - 0.05)  # = 95.0
        # / create data where close at bar 10 is exactly at stop price
        n = 50
        dates = pd.bdate_range("2024-01-02", periods=n, freq="B")
        close = np.full(n, 98.0)
        close[10] = expected_stop  # exactly at stop
        close[11] = expected_stop - 0.01  # below stop
        high = close + 1.0
        low = close - 1.0
        open_ = close + 0.5
        volume = np.full(n, 1_000_000.0)
        data = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=dates,
        )
        entry_date = dates[0]
        # / at stop price (<=) should trigger
        result = s.should_exit("AAPL", data, entry_price, entry_date, 10)
        assert result.should_exit is True
        assert "stop loss" in result.reason

    def test_stop_not_triggered_when_price_above_stop(self):
        # / price just above stop should not trigger
        cfg = _base_config(exit_conditions={
            "stop_loss": {"type": "fixed_pct", "pct": 0.05},
        })
        s = ConfigDrivenStrategy(cfg)
        entry_price = 100.0
        stop_price = entry_price * (1 - 0.05)  # = 95.0
        n = 50
        dates = pd.bdate_range("2024-01-02", periods=n, freq="B")
        close = np.full(n, 96.0)  # above 95.0
        high = close + 1.0
        low = close - 1.0
        open_ = close + 0.5
        volume = np.full(n, 1_000_000.0)
        data = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=dates,
        )
        entry_date = dates[0]
        result = s.should_exit("AAPL", data, entry_price, entry_date, 10)
        assert result.should_exit is False


# ---------------------------------------------------------------------------
# / deep failure-pinpointing tests — exit time exit
# ---------------------------------------------------------------------------

class TestExitTimeExitDeep:
    def test_exactly_at_max_holding_days(self):
        # / days_held >= max_days should trigger; test with exactly max_days
        cfg = _base_config(exit_conditions={
            "time_exit": {"max_holding_days": 10},
        })
        s = ConfigDrivenStrategy(cfg)
        # / build data with known calendar day spacing
        n = 30
        dates = pd.bdate_range("2024-01-02", periods=n, freq="B")
        close = np.full(n, 100.0)
        high = close + 1.0
        low = close - 1.0
        open_ = close + 0.5
        volume = np.full(n, 1_000_000.0)
        data = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=dates,
        )
        entry_date = dates[0]
        # / find the first bar where calendar days >= 10
        for i in range(len(dates)):
            days_held = (dates[i] - entry_date).days
            if days_held == 10 or (days_held > 10 and i > 0 and (dates[i - 1] - entry_date).days < 10):
                result = s.should_exit("AAPL", data, 100.0, entry_date, i)
                assert result.should_exit is True
                assert "time exit" in result.reason
                break

    def test_one_day_before_max(self):
        # / days_held < max_days should not trigger
        cfg = _base_config(exit_conditions={
            "time_exit": {"max_holding_days": 10},
        })
        s = ConfigDrivenStrategy(cfg)
        n = 30
        dates = pd.bdate_range("2024-01-02", periods=n, freq="B")
        close = np.full(n, 100.0)
        high = close + 1.0
        low = close - 1.0
        open_ = close + 0.5
        volume = np.full(n, 1_000_000.0)
        data = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=dates,
        )
        entry_date = dates[0]
        # / find the last bar where days_held < 10 (i.e., days_held == 9 or less)
        for i in range(len(dates)):
            days_held = (dates[i] - entry_date).days
            if days_held == 9:
                result = s.should_exit("AAPL", data, 100.0, entry_date, i)
                assert result.should_exit is False
                break


# ---------------------------------------------------------------------------
# / deep failure-pinpointing tests — fundamental filters
# ---------------------------------------------------------------------------

class TestFundamentalFiltersDeep:
    def test_all_filters_together_pass(self):
        # / config with all filter fields set, analysis that passes all
        cfg = _base_config(
            fundamental_filters={
                "pe_ratio_max": 40,
                "pe_vs_sector": "below_average",
                "revenue_growth_min": 0.01,
                "fcf_margin_min": 0.10,
                "debt_to_equity_max": 2.0,
                "dcf_upside_min": 0.05,
                "insider_buying_recent": True,
            },
            entry_conditions={"operator": "AND", "signals": []},
        )
        s = ConfigDrivenStrategy(cfg)
        analysis = AnalysisData(
            pe_ratio=20.0,
            sector_pe_avg=25.0,
            revenue_growth=0.10,
            fcf_margin=0.20,
            debt_to_equity=0.8,
            dcf_upside=0.15,
            insider_net_buy_ratio=0.5,
        )
        data = _ohlcv(120)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is True
        assert "fundamentals passed" in result.reasons

    def test_insider_buying_recent_true_requires_positive_ratio(self):
        # / insider_buying_recent=True requires insider_net_buy_ratio > 0
        cfg = _base_config(
            fundamental_filters={"insider_buying_recent": True},
            entry_conditions={"operator": "AND", "signals": []},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(120)

        # / ratio = 0 should fail (insider_net_buy_ratio <= 0)
        analysis_zero = AnalysisData(insider_net_buy_ratio=0.0)
        result_zero = s.should_enter("AAPL", data, analysis_zero)
        assert result_zero.should_enter is False
        assert any("insider" in r for r in result_zero.reasons)

        # / negative ratio should fail
        analysis_neg = AnalysisData(insider_net_buy_ratio=-0.3)
        result_neg = s.should_enter("AAPL", data, analysis_neg)
        assert result_neg.should_enter is False

        # / positive ratio should pass
        analysis_pos = AnalysisData(insider_net_buy_ratio=0.1)
        result_pos = s.should_enter("AAPL", data, analysis_pos)
        assert result_pos.should_enter is True

    def test_dcf_upside_exact_boundary(self):
        # / dcf_upside exactly at min should pass (not < min)
        cfg = _base_config(
            fundamental_filters={"dcf_upside_min": 0.10},
            entry_conditions={"operator": "AND", "signals": []},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(120)

        # / exactly at boundary: 0.10 is NOT < 0.10, so should pass
        analysis_at = AnalysisData(dcf_upside=0.10)
        result_at = s.should_enter("AAPL", data, analysis_at)
        assert result_at.should_enter is True

        # / just below: 0.099 < 0.10, should fail
        analysis_below = AnalysisData(dcf_upside=0.099)
        result_below = s.should_enter("AAPL", data, analysis_below)
        assert result_below.should_enter is False
        assert any("dcf" in r for r in result_below.reasons)


# ---------------------------------------------------------------------------
# / bear market overrides
# ---------------------------------------------------------------------------

class TestBearMarketOverrides:
    def test_no_regime_uses_base(self):
        # / without regime, base fundamental filters apply
        cfg = _base_config(
            fundamental_filters={"pe_ratio_max": 30, "dcf_upside_min": 0.10},
            bear_market_overrides={
                "fundamental_filters": {"pe_ratio_max": 20},
            },
            entry_conditions={"operator": "AND", "signals": []},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(120)
        # / pe=25 passes base (max=30) but would fail bear override (max=20)
        analysis = AnalysisData(pe_ratio=25.0, dcf_upside=0.15)
        result = s.should_enter("AAPL", data, analysis)
        # / no regime -> base filter pe_ratio_max=30 used, 25 < 30 passes
        assert not any("pe" in r.lower() and "max" in r.lower() for r in result.reasons)

    def test_bear_regime_uses_overrides(self):
        # / bear regime should apply overrides
        cfg = _base_config(
            fundamental_filters={"pe_ratio_max": 30, "dcf_upside_min": 0.10},
            bear_market_overrides={
                "fundamental_filters": {"pe_ratio_max": 20},
            },
            entry_conditions={"operator": "AND", "signals": []},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(120)
        # / pe=25 fails bear override (max=20), regime=bear
        analysis = AnalysisData(pe_ratio=25.0, dcf_upside=0.15, regime="bear")
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False
        assert any("pe" in r.lower() for r in result.reasons)

    def test_bull_regime_ignores_overrides(self):
        # / bull regime should use base filters, not bear overrides
        cfg = _base_config(
            fundamental_filters={"pe_ratio_max": 30, "dcf_upside_min": 0.10},
            bear_market_overrides={
                "fundamental_filters": {"pe_ratio_max": 20},
            },
            entry_conditions={"operator": "AND", "signals": []},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(120)
        # / pe=25, regime=bull -> base filter used (max=30), passes
        analysis = AnalysisData(pe_ratio=25.0, dcf_upside=0.15, regime="bull")
        result = s.should_enter("AAPL", data, analysis)
        assert not any("pe" in r.lower() and "max" in r.lower() for r in result.reasons)

    def test_effective_bypass_consensus_bear(self):
        # / bear override sets bypass_consensus=True
        cfg = _base_config(
            bypass_consensus=False,
            bear_market_overrides={"bypass_consensus": True},
        )
        s = ConfigDrivenStrategy(cfg)
        assert s.get_effective_bypass_consensus(regime=None) is False
        assert s.get_effective_bypass_consensus(regime="bull") is False
        assert s.get_effective_bypass_consensus(regime="bear") is True

    def test_effective_bypass_consensus_no_override(self):
        # / no bear override -> base value used in all regimes
        cfg = _base_config(bypass_consensus=True)
        s = ConfigDrivenStrategy(cfg)
        assert s.get_effective_bypass_consensus(regime=None) is True
        assert s.get_effective_bypass_consensus(regime="bear") is True
        assert s.get_effective_bypass_consensus(regime="bull") is True


# ---------------------------------------------------------------------------
# / multi-timeframe entry tests
# ---------------------------------------------------------------------------

class TestMultiTimeframeEntry:
    def test_daily_signal_no_intraday_backward_compat(self):
        # / signals without timeframe field work identically with or without intraday_df
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [{"indicator": "sma", "condition": "price_above", "period": 20}],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150)
        r1 = s.should_enter("AAPL", data)
        r2 = s.should_enter("AAPL", data, intraday_df=data)
        assert r1.should_enter == r2.should_enter
        assert r1.strength == pytest.approx(r2.strength)

    def test_intraday_signal_uses_intraday_df(self):
        # / signal with timeframe="2h" evaluates against intraday_df, not daily
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [
                {"indicator": "sma", "condition": "price_above", "period": 20, "timeframe": "2h"},
            ],
        })
        s = ConfigDrivenStrategy(cfg)
        daily = _ohlcv(150, base=100.0)
        intraday = _ohlcv(150, base=80.0, trend=0.50)
        result = s.should_enter("AAPL", daily, intraday_df=intraday)
        assert result.should_enter is True
        assert "[2h]" in result.reasons[0]

    def test_intraday_signal_no_data_skipped(self):
        # / signal with timeframe="2h" but no intraday_df -> skipped as failed
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [
                {"indicator": "rsi", "condition": "below", "threshold": 30, "timeframe": "2h"},
            ],
        })
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(150)
        result = s.should_enter("AAPL", data, intraday_df=None)
        assert result.should_enter is False
        assert "no 2h data" in result.reasons[0]

    def test_intraday_signal_insufficient_bars_skipped(self):
        # / intraday_df with <14 bars -> signal skipped
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [
                {"indicator": "rsi", "condition": "below", "threshold": 30, "timeframe": "2h"},
            ],
        })
        s = ConfigDrivenStrategy(cfg)
        daily = _ohlcv(150)
        short_intraday = _ohlcv(10)
        result = s.should_enter("AAPL", daily, intraday_df=short_intraday)
        assert result.should_enter is False

    def test_mixed_timeframe_and_operator(self):
        # / AND with one daily signal and one 2h signal
        cfg = _base_config(entry_conditions={
            "operator": "AND",
            "signals": [
                {"indicator": "sma", "condition": "price_above", "period": 20},
                {"indicator": "sma", "condition": "price_above", "period": 20, "timeframe": "2h"},
            ],
        })
        s = ConfigDrivenStrategy(cfg)
        rising = _ohlcv_rising(150)
        result = s.should_enter("AAPL", rising, intraday_df=rising)
        assert result.should_enter is True

    def test_timeframe_case_insensitive_daily(self):
        # / "1D", "Daily", "1day" all treated as daily
        for tf in ["1d", "1D", "daily", "Daily", "1Day", "1DAY"]:
            cfg = _base_config(entry_conditions={
                "operator": "AND",
                "signals": [{"indicator": "sma", "condition": "price_above", "period": 20, "timeframe": tf}],
            })
            s = ConfigDrivenStrategy(cfg)
            data = _ohlcv_rising(150)
            result = s.should_enter("AAPL", data)
            # / daily timeframes should evaluate against daily data, not skip
            assert "skipped" not in str(result.reasons), f"timeframe={tf} wrongly treated as intraday"


# ---------------------------------------------------------------------------
# / strict_data fundamental tolerance tests
# ---------------------------------------------------------------------------

class TestStrictDataTolerance:
    def test_strict_data_true_rejects_missing(self):
        # / default strict_data=true: missing pe_ratio rejects
        cfg = _base_config(
            fundamental_filters={"pe_ratio_max": 30, "strict_data": True},
            entry_conditions={"operator": "AND", "signals": [{"indicator": "sma", "condition": "price_above", "period": 20}]},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(150)
        analysis = AnalysisData(pe_ratio=None)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False
        assert "pe_ratio data unavailable" in str(result.reasons)

    def test_strict_data_false_skips_missing(self):
        # / strict_data=false: missing pe_ratio skips filter, continues evaluation
        cfg = _base_config(
            fundamental_filters={"pe_ratio_max": 30, "strict_data": False},
            entry_conditions={"operator": "AND", "signals": [{"indicator": "sma", "condition": "price_above", "period": 20}]},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150)
        analysis = AnalysisData(pe_ratio=None)
        result = s.should_enter("AAPL", data, analysis)
        # / fundamentals pass because missing data is tolerated
        assert result.should_enter is True

    def test_strict_data_false_still_rejects_present_bad_data(self):
        # / strict_data=false doesn't skip check when data IS present and fails
        cfg = _base_config(
            fundamental_filters={"pe_ratio_max": 30, "strict_data": False},
            entry_conditions={"operator": "AND", "signals": [{"indicator": "sma", "condition": "price_above", "period": 20}]},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv(150)
        analysis = AnalysisData(pe_ratio=50.0)  # / above 30 max
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False
        assert "pe 50.0 > max 30" in str(result.reasons)


# ---------------------------------------------------------------------------
# / phase 4: filter handler registry — each new filter key has a registered handler
# / that delivers the same behaviour pattern as the legacy if-chain
# ---------------------------------------------------------------------------

class TestFilterHandlerRegistry:
    def test_registry_is_populated(self):
        from src.strategies.base_strategy import FILTER_HANDLERS
        # / legacy handlers still registered
        assert "pe_ratio_max" in FILTER_HANDLERS
        assert "pe_vs_sector" in FILTER_HANDLERS
        assert "revenue_growth_min" in FILTER_HANDLERS
        assert "fcf_margin_min" in FILTER_HANDLERS
        assert "debt_to_equity_max" in FILTER_HANDLERS
        assert "dcf_upside_min" in FILTER_HANDLERS
        assert "insider_buying_recent" in FILTER_HANDLERS
        # / new phase 4 handlers
        assert "macro_score_min" in FILTER_HANDLERS
        assert "congressional_buy_ratio_min" in FILTER_HANDLERS
        assert "analyst_consensus_min" in FILTER_HANDLERS
        assert "price_target_upside_min" in FILTER_HANDLERS
        assert "earnings_revision_momentum_min" in FILTER_HANDLERS
        assert "short_pct_float_max" in FILTER_HANDLERS
        assert "dark_pool_ratio_max" in FILTER_HANDLERS
        assert "iv_rank_min" in FILTER_HANDLERS
        assert "iv_rank_max" in FILTER_HANDLERS
        assert "put_call_ratio_max" in FILTER_HANDLERS

    def test_register_filter_decorator_adds_handler(self):
        from src.strategies._filters import FILTER_HANDLERS, register_filter

        @register_filter("custom_filter_test")
        def _handler(analysis, threshold, filters):
            return True, ""

        assert "custom_filter_test" in FILTER_HANDLERS
        # / cleanup
        del FILTER_HANDLERS["custom_filter_test"]


def _technical_sma_signals() -> list:
    # / simple passing signal to skip past technicals in fundamental-only tests
    return [{"indicator": "sma", "condition": "price_above", "period": 20}]


class TestMacroScoreFilter:
    def _cfg(self, **filters):
        return _base_config(
            fundamental_filters=filters,
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )

    def test_passes_when_score_above_threshold(self):
        s = ConfigDrivenStrategy(self._cfg(macro_score_min=0.1))
        data = _ohlcv_rising(150, seed=42)
        analysis = AnalysisData(macro_score=0.5)
        result = s.should_enter("AAPL", data, analysis)
        # / no macro rejection; technicals may still fail but not fundamentals
        assert not any("macro" in r for r in result.reasons)

    def test_rejects_when_score_below_threshold(self):
        s = ConfigDrivenStrategy(self._cfg(macro_score_min=0.1))
        data = _ohlcv_rising(150, seed=42)
        analysis = AnalysisData(macro_score=-0.2)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False
        assert any("macro" in r for r in result.reasons)

    def test_strict_true_rejects_missing(self):
        s = ConfigDrivenStrategy(self._cfg(macro_score_min=0.1, strict_data=True))
        data = _ohlcv_rising(150, seed=42)
        analysis = AnalysisData(macro_score=None)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False
        assert any("macro_score" in r for r in result.reasons)

    def test_strict_false_skips_missing(self):
        s = ConfigDrivenStrategy(self._cfg(macro_score_min=0.1, strict_data=False))
        data = _ohlcv_rising(150, seed=42)
        analysis = AnalysisData(macro_score=None)
        result = s.should_enter("AAPL", data, analysis)
        # / macro filter should not cause rejection
        assert not any("macro_score" in r for r in result.reasons)


class TestCongressionalBuyRatioFilter:
    def test_passes_above_threshold(self):
        cfg = _base_config(
            fundamental_filters={"congressional_buy_ratio_min": 0.3},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150, seed=42)
        analysis = AnalysisData(congressional_buy_ratio=0.5)
        result = s.should_enter("AAPL", data, analysis)
        assert not any("congressional" in r for r in result.reasons)

    def test_rejects_below_threshold(self):
        cfg = _base_config(
            fundamental_filters={"congressional_buy_ratio_min": 0.3},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150, seed=42)
        analysis = AnalysisData(congressional_buy_ratio=0.1)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False
        assert any("congressional" in r for r in result.reasons)


class TestAnalystConsensusFilter:
    def test_rejects_below_min(self):
        cfg = _base_config(
            fundamental_filters={"analyst_consensus_min": 0.5},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150, seed=42)
        analysis = AnalysisData(analyst_consensus=0.2)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False
        assert any("analyst" in r for r in result.reasons)

    def test_passes_at_min(self):
        cfg = _base_config(
            fundamental_filters={"analyst_consensus_min": 0.5},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150, seed=42)
        analysis = AnalysisData(analyst_consensus=0.5)
        result = s.should_enter("AAPL", data, analysis)
        assert not any("analyst" in r for r in result.reasons)


class TestPriceTargetUpsideFilter:
    def test_rejects_below_threshold(self):
        cfg = _base_config(
            fundamental_filters={"price_target_upside_min": 0.10},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150, seed=42)
        analysis = AnalysisData(price_target_upside=0.03)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False
        assert any("price target upside" in r for r in result.reasons)


class TestEarningsRevisionMomentumFilter:
    def test_rejects_below_threshold(self):
        cfg = _base_config(
            fundamental_filters={"earnings_revision_momentum_min": 0.05},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150, seed=42)
        analysis = AnalysisData(earnings_revision_momentum=-0.02)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False
        assert any("earnings revision momentum" in r for r in result.reasons)


class TestShortPctFloatFilter:
    def test_rejects_above_max(self):
        cfg = _base_config(
            fundamental_filters={"short_pct_float_max": 0.20},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150, seed=42)
        analysis = AnalysisData(short_pct_float=0.35)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False
        assert any("short pct float" in r for r in result.reasons)

    def test_passes_below_max(self):
        cfg = _base_config(
            fundamental_filters={"short_pct_float_max": 0.20},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150, seed=42)
        analysis = AnalysisData(short_pct_float=0.10)
        result = s.should_enter("AAPL", data, analysis)
        assert not any("short pct float" in r for r in result.reasons)


class TestDarkPoolRatioFilter:
    def test_rejects_above_max(self):
        cfg = _base_config(
            fundamental_filters={"dark_pool_ratio_max": 0.4},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150, seed=42)
        analysis = AnalysisData(dark_pool_ratio=0.6)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False
        assert any("dark pool ratio" in r for r in result.reasons)


class TestIVRankFilter:
    def test_iv_rank_min_rejects_low(self):
        cfg = _base_config(
            fundamental_filters={"iv_rank_min": 0.3},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150, seed=42)
        analysis = AnalysisData(iv_rank=0.1)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False
        assert any("iv rank" in r for r in result.reasons)

    def test_iv_rank_max_rejects_high(self):
        cfg = _base_config(
            fundamental_filters={"iv_rank_max": 0.7},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150, seed=42)
        analysis = AnalysisData(iv_rank=0.9)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False
        assert any("iv rank" in r for r in result.reasons)

    def test_iv_rank_min_and_max_band_passes(self):
        cfg = _base_config(
            fundamental_filters={"iv_rank_min": 0.2, "iv_rank_max": 0.8},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150, seed=42)
        analysis = AnalysisData(iv_rank=0.5)
        result = s.should_enter("AAPL", data, analysis)
        assert not any("iv rank" in r for r in result.reasons)


class TestPutCallRatioFilter:
    def test_rejects_above_max(self):
        cfg = _base_config(
            fundamental_filters={"put_call_ratio_max": 1.0},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150, seed=42)
        analysis = AnalysisData(put_call_ratio=1.5)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False
        assert any("put/call" in r for r in result.reasons)


class TestLegacyHandlersStillWork:
    # / after refactor, every filter that was in the if-chain must still behave identically
    def test_pe_ratio_max_still_enforced(self):
        cfg = _base_config(
            fundamental_filters={"pe_ratio_max": 20},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150)
        analysis = AnalysisData(pe_ratio=25.0)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False
        assert any("pe 25.0 > max 20" in r for r in result.reasons)

    def test_pe_vs_sector_still_enforced(self):
        cfg = _base_config(
            fundamental_filters={"pe_vs_sector": "below_average"},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150)
        analysis = AnalysisData(pe_ratio=30.0, sector_pe_avg=20.0)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False

    def test_revenue_growth_min_still_enforced(self):
        cfg = _base_config(
            fundamental_filters={"revenue_growth_min": 0.05},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150)
        analysis = AnalysisData(revenue_growth=0.01)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False

    def test_insider_buying_recent_still_enforced(self):
        cfg = _base_config(
            fundamental_filters={"insider_buying_recent": True},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150)
        # / insider_buying_recent=True with ratio<=0 rejects
        analysis = AnalysisData(insider_net_buy_ratio=-0.5)
        result = s.should_enter("AAPL", data, analysis)
        assert result.should_enter is False
        assert any("insider" in r for r in result.reasons)

    def test_nvt_max_tolerates_none(self):
        # / crypto filter keeps legacy None-tolerance: missing data passes
        cfg = _base_config(
            fundamental_filters={"nvt_max": 20.0},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150)
        analysis = AnalysisData(nvt_ratio=None)
        result = s.should_enter("AAPL", data, analysis)
        assert not any("nvt" in r for r in result.reasons)

    def test_unknown_filter_key_silently_skipped(self):
        # / unknown keys shouldn't crash — they're just no-ops
        cfg = _base_config(
            fundamental_filters={"invented_filter_xyz": 0.5},
            entry_conditions={"operator": "AND", "signals": _technical_sma_signals()},
        )
        s = ConfigDrivenStrategy(cfg)
        data = _ohlcv_rising(150)
        analysis = AnalysisData()
        result = s.should_enter("AAPL", data, analysis)
        # / neither rejects nor crashes
        assert isinstance(result, EntrySignal)

# / tests for momentum indicators

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.indicators.momentum import (
    StochasticResult,
    cci,
    roc,
    rsi,
    stochastic,
    williams_r,
)


def _price_series(n: int = 100, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    trend = np.linspace(100, 130, n)
    noise = rng.normal(0, 2, n)
    return pd.Series(trend + noise)


def _ohlc(n: int = 100, seed: int = 42) -> tuple[pd.Series, pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    close = np.linspace(100, 130, n) + rng.normal(0, 2, n)
    high = close + rng.uniform(0.5, 2.0, n)
    low = close - rng.uniform(0.5, 2.0, n)
    return pd.Series(high), pd.Series(low), pd.Series(close)


class TestRSI:
    def test_range_0_to_100(self):
        s = _price_series(100)
        result = rsi(s)
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_uptrend_high_rsi(self):
        # / strong uptrend with noise, enough points for wilder smoothing
        rng = np.random.default_rng(42)
        s = pd.Series(np.linspace(100, 200, 100) + rng.normal(0, 0.5, 100))
        result = rsi(s, period=14)
        last = result.dropna().iloc[-1]
        assert last > 70

    def test_downtrend_low_rsi(self):
        # / strong downtrend
        s = pd.Series(np.linspace(200, 100, 50))
        result = rsi(s, period=14)
        last = result.dropna().iloc[-1]
        assert last < 30

    def test_nan_for_insufficient_data(self):
        s = pd.Series([1.0, 2.0, 3.0])
        result = rsi(s, period=14)
        assert all(pd.isna(result))

    def test_flat_market_mid_rsi(self):
        # / flat market rsi should be near 50
        rng = np.random.default_rng(42)
        s = pd.Series(100 + rng.normal(0, 0.5, 100))
        result = rsi(s, period=14)
        last = result.dropna().iloc[-1]
        assert 30 < last < 70


class TestStochastic:
    def test_returns_dataclass(self):
        h, l, c = _ohlc(50)
        result = stochastic(h, l, c)
        assert isinstance(result, StochasticResult)
        assert len(result.k) == 50
        assert len(result.d) == 50

    def test_range_0_to_100(self):
        h, l, c = _ohlc(100)
        result = stochastic(h, l, c)
        valid_k = result.k.dropna()
        assert (valid_k >= 0).all()
        assert (valid_k <= 100).all()

    def test_high_at_top(self):
        # / when close is at the high end, %k should be high
        n = 30
        high = pd.Series([110.0] * n)
        low = pd.Series([90.0] * n)
        close = pd.Series([109.0] * n)
        result = stochastic(high, low, close, k_period=14)
        last_k = result.k.dropna().iloc[-1]
        assert last_k > 80


class TestCCI:
    def test_basic(self):
        h, l, c = _ohlc(100)
        result = cci(h, l, c)
        valid = result.dropna()
        assert len(valid) > 0

    def test_length_preserved(self):
        h, l, c = _ohlc(50)
        result = cci(h, l, c, period=20)
        assert len(result) == 50


class TestWilliamsR:
    def test_range_minus100_to_0(self):
        h, l, c = _ohlc(100)
        result = williams_r(h, l, c)
        valid = result.dropna()
        assert (valid >= -100).all()
        assert (valid <= 0).all()

    def test_at_high_near_zero(self):
        n = 30
        high = pd.Series([110.0] * n)
        low = pd.Series([90.0] * n)
        close = pd.Series([110.0] * n)
        result = williams_r(high, low, close, period=14)
        last = result.dropna().iloc[-1]
        assert last == pytest.approx(0.0, abs=1.0)


class TestROC:
    def test_basic(self):
        s = pd.Series([100.0, 110.0, 121.0])
        result = roc(s, period=1)
        # / 110/100 - 1 = 10%
        assert result.iloc[1] == pytest.approx(10.0)

    def test_longer_period(self):
        s = pd.Series([100.0, 105.0, 110.0, 120.0, 130.0])
        result = roc(s, period=2)
        # / 110/100 - 1 = 10%
        assert result.iloc[2] == pytest.approx(10.0)

    def test_negative_roc(self):
        s = pd.Series([100.0, 90.0])
        result = roc(s, period=1)
        assert result.iloc[1] == pytest.approx(-10.0)


# ---------- new deep tests ----------


class TestRSIDeep:
    def test_all_up_approaches_100(self):
        # / mostly increasing with tiny noise so avg_loss > 0 to avoid nan
        rng = np.random.default_rng(42)
        s = pd.Series(np.linspace(100, 200, 100) + rng.normal(0, 0.5, 100))
        result = rsi(s, period=14)
        last = result.dropna().iloc[-1]
        assert last > 95

    def test_all_down_approaches_0(self):
        # / mostly decreasing with tiny noise so avg_gain > 0 to avoid nan
        rng = np.random.default_rng(42)
        s = pd.Series(np.linspace(200, 100, 100) + rng.normal(0, 0.5, 100))
        result = rsi(s, period=14)
        last = result.dropna().iloc[-1]
        assert last < 5

    def test_constant_price_nan(self):
        # / constant price -> zero gain and zero loss -> rsi is nan (div by zero)
        s = pd.Series([50.0] * 30)
        result = rsi(s, period=14)
        valid = result.dropna()
        # / all should be nan because avg_loss is 0 -> rs = gain/nan -> nan
        assert len(valid) == 0

    def test_hand_computed_14_period(self):
        # / 14 ups of +1 then 14 downs of -1
        # / first valid rsi at idx 15 (need 14 diffs + min_periods=14)
        up = np.arange(100.0, 115.0)  # 15 values: 100..114
        down = np.arange(114.0, 99.0, -1.0)  # 16 values: 114..99
        s = pd.Series(np.concatenate([up, down[1:]]))
        result = rsi(s, period=14)
        # / at idx 15, first down bar just started, rsi should still be very high
        assert result.iloc[15] > 85


class TestStochasticDeep:
    def test_at_new_high_k_100(self):
        # / close at highest high -> %k = 100
        n = 20
        high = pd.Series([100.0 + i for i in range(n)])
        low = pd.Series([90.0 + i for i in range(n)])
        close = pd.Series([100.0 + i for i in range(n)])  # close == high
        result = stochastic(high, low, close, k_period=14)
        last_k = result.k.dropna().iloc[-1]
        assert last_k == pytest.approx(100.0)

    def test_at_new_low_k_0(self):
        # / close at lowest low -> %k = 0
        n = 20
        high = pd.Series([110.0 - i for i in range(n)])
        low = pd.Series([100.0 - i for i in range(n)])
        close = pd.Series([100.0 - i for i in range(n)])  # close == low
        result = stochastic(high, low, close, k_period=14)
        last_k = result.k.dropna().iloc[-1]
        assert last_k == pytest.approx(0.0)

    def test_high_equals_low_nan(self):
        # / high == low -> denom = 0 -> nan
        n = 20
        high = pd.Series([100.0] * n)
        low = pd.Series([100.0] * n)
        close = pd.Series([100.0] * n)
        result = stochastic(high, low, close, k_period=14)
        k_after = result.k.iloc[13:]
        assert all(pd.isna(k_after))

    def test_d_is_sma_of_k(self):
        # / %d should be sma(3) of %k
        h, l, c = _ohlc(50)
        result = stochastic(h, l, c, k_period=14, d_period=3)
        expected_d = result.k.rolling(window=3, min_periods=3).mean()
        valid_idx = result.d.dropna().index
        np.testing.assert_allclose(
            result.d[valid_idx].values,
            expected_d[valid_idx].values,
            atol=1e-10,
        )


class TestCCIDeep:
    def test_all_equal_typical_prices_nan(self):
        # / all tp equal -> mad = 0 -> nan
        n = 30
        high = pd.Series([100.0] * n)
        low = pd.Series([100.0] * n)
        close = pd.Series([100.0] * n)
        result = cci(high, low, close, period=20)
        valid = result.iloc[19:]
        assert all(pd.isna(valid))

    def test_overbought_above_100(self):
        # / strong uptrend should give cci > 100
        n = 50
        close = pd.Series(np.linspace(100, 200, n))
        high = close + 1
        low = close - 1
        result = cci(high, low, close, period=20)
        last = result.dropna().iloc[-1]
        assert last > 100

    def test_oversold_below_minus_100(self):
        # / strong downtrend should give cci < -100
        n = 50
        close = pd.Series(np.linspace(200, 100, n))
        high = close + 1
        low = close - 1
        result = cci(high, low, close, period=20)
        last = result.dropna().iloc[-1]
        assert last < -100


class TestWilliamsRDeep:
    def test_at_period_high_zero(self):
        # / close at period high -> williams_r = 0
        n = 20
        high = pd.Series([100.0 + i for i in range(n)])
        low = pd.Series([90.0 + i for i in range(n)])
        close = high.copy()  # close at high
        result = williams_r(high, low, close, period=14)
        last = result.dropna().iloc[-1]
        assert last == pytest.approx(0.0)

    def test_at_period_low_minus_100(self):
        # / close at period low -> williams_r = -100
        n = 20
        high = pd.Series([110.0 - i for i in range(n)])
        low = pd.Series([100.0 - i for i in range(n)])
        close = low.copy()  # close at low
        result = williams_r(high, low, close, period=14)
        last = result.dropna().iloc[-1]
        assert last == pytest.approx(-100.0)

    def test_bounds_all_values(self):
        # / williams_r bounds [-100, 0] for all valid values
        h, l, c = _ohlc(100)
        result = williams_r(h, l, c, period=14)
        valid = result.dropna()
        assert (valid >= -100).all()
        assert (valid <= 0).all()


class TestROCDeep:
    def test_known_value_100_to_110(self):
        # / price 100 -> 110 in 1 period -> roc = 10.0%
        s = pd.Series([100.0, 110.0])
        result = roc(s, period=1)
        assert result.iloc[1] == pytest.approx(10.0)

    def test_zero_price_nan(self):
        # / zero price should return nan (division by zero)
        s = pd.Series([0.0, 100.0])
        result = roc(s, period=1)
        # / prev=0 -> nan
        assert pd.isna(result.iloc[1])

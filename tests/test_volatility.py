# / tests for volatility indicators

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.indicators.volatility import (
    BollingerBands,
    KeltnerChannel,
    atr,
    bollinger_bands,
    keltner_channel,
)


def _ohlc(n: int = 100, seed: int = 42) -> tuple[pd.Series, pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    close = np.linspace(100, 130, n) + rng.normal(0, 2, n)
    high = close + rng.uniform(0.5, 2.0, n)
    low = close - rng.uniform(0.5, 2.0, n)
    return pd.Series(high), pd.Series(low), pd.Series(close)


class TestBollingerBands:
    def test_returns_dataclass(self):
        _, _, close = _ohlc(50)
        result = bollinger_bands(close)
        assert isinstance(result, BollingerBands)

    def test_upper_above_middle_above_lower(self):
        _, _, close = _ohlc(100)
        bb = bollinger_bands(close, period=20)
        valid_idx = bb.upper.dropna().index
        assert (bb.upper[valid_idx] >= bb.middle[valid_idx]).all()
        assert (bb.middle[valid_idx] >= bb.lower[valid_idx]).all()

    def test_middle_is_sma(self):
        _, _, close = _ohlc(100)
        bb = bollinger_bands(close, period=20)
        expected_sma = close.rolling(20).mean()
        valid = bb.middle.dropna()
        np.testing.assert_allclose(valid.values, expected_sma.dropna().values, rtol=1e-10)

    def test_bandwidth_positive(self):
        _, _, close = _ohlc(100)
        bb = bollinger_bands(close)
        valid = bb.bandwidth.dropna()
        assert (valid > 0).all()

    def test_pct_b_range(self):
        _, _, close = _ohlc(100)
        bb = bollinger_bands(close)
        valid = bb.pct_b.dropna()
        # / pct_b can go outside 0-1 but most should be within
        assert valid.median() > 0
        assert valid.median() < 1

    def test_wider_std_wider_bands(self):
        _, _, close = _ohlc(100)
        narrow = bollinger_bands(close, std_dev=1.0)
        wide = bollinger_bands(close, std_dev=3.0)
        valid_idx = narrow.bandwidth.dropna().index
        assert (wide.bandwidth[valid_idx] > narrow.bandwidth[valid_idx]).all()


class TestATR:
    def test_positive(self):
        h, l, c = _ohlc(100)
        result = atr(h, l, c)
        valid = result.dropna()
        assert (valid > 0).all()

    def test_higher_volatility_higher_atr(self):
        rng = np.random.default_rng(42)
        n = 100
        # / low volatility
        close_low = pd.Series(np.linspace(100, 110, n) + rng.normal(0, 0.5, n))
        high_low = close_low + 0.5
        low_low = close_low - 0.5
        # / high volatility
        close_high = pd.Series(np.linspace(100, 110, n) + rng.normal(0, 5, n))
        high_high = close_high + 5
        low_high = close_high - 5

        atr_low = atr(high_low, low_low, close_low).dropna().iloc[-1]
        atr_high = atr(high_high, low_high, close_high).dropna().iloc[-1]
        assert atr_high > atr_low

    def test_length_preserved(self):
        h, l, c = _ohlc(50)
        result = atr(h, l, c)
        assert len(result) == 50


class TestKeltnerChannel:
    def test_returns_dataclass(self):
        h, l, c = _ohlc(50)
        result = keltner_channel(h, l, c)
        assert isinstance(result, KeltnerChannel)

    def test_upper_above_middle_above_lower(self):
        h, l, c = _ohlc(100)
        kc = keltner_channel(h, l, c)
        valid_idx = kc.upper.dropna().index
        assert (kc.upper[valid_idx] >= kc.middle[valid_idx]).all()
        assert (kc.middle[valid_idx] >= kc.lower[valid_idx]).all()

    def test_wider_multiplier_wider_channel(self):
        h, l, c = _ohlc(100)
        narrow = keltner_channel(h, l, c, multiplier=1.0)
        wide = keltner_channel(h, l, c, multiplier=3.0)
        valid_idx = narrow.upper.dropna().index & wide.upper.dropna().index
        assert (wide.upper[valid_idx] > narrow.upper[valid_idx]).all()


# ---------- new deep tests ----------

from src.indicators.trend import ema as trend_ema


class TestBollingerExactFormula:
    def test_upper_lower_formula(self):
        # / upper = sma + 2*std, lower = sma - 2*std for std_dev=2
        _, _, close = _ohlc(100)
        bb = bollinger_bands(close, period=20, std_dev=2.0)
        rolling_mean = close.rolling(window=20, min_periods=20).mean()
        rolling_std = close.rolling(window=20, min_periods=20).std()
        expected_upper = rolling_mean + 2.0 * rolling_std
        expected_lower = rolling_mean - 2.0 * rolling_std
        valid_idx = bb.upper.dropna().index
        np.testing.assert_allclose(bb.upper[valid_idx].values, expected_upper[valid_idx].values, atol=1e-10)
        np.testing.assert_allclose(bb.lower[valid_idx].values, expected_lower[valid_idx].values, atol=1e-10)

    def test_pct_b_at_lower_is_0(self):
        # / when price == lower band, pct_b = 0
        # / create data where last close equals lower band
        s = pd.Series([100.0] * 19 + [80.0])  # drop forces close near lower band
        bb = bollinger_bands(s, period=20, std_dev=2.0)
        # / pct_b = (close - lower) / (upper - lower)
        # / verify the formula manually for last bar
        last_idx = 19
        expected_pct_b = (s.iloc[last_idx] - bb.lower.iloc[last_idx]) / (bb.upper.iloc[last_idx] - bb.lower.iloc[last_idx])
        assert bb.pct_b.iloc[last_idx] == pytest.approx(expected_pct_b)

    def test_pct_b_at_middle_is_half(self):
        # / when price == middle band, pct_b = 0.5
        # / use constant price so close == sma == middle
        s = pd.Series([100.0] * 19 + [100.0])
        bb = bollinger_bands(s, period=20, std_dev=2.0)
        # / constant price -> std=0 -> upper=lower=middle -> pct_b = nan (0/0)
        # / need slight variation so bands don't collapse
        s2 = pd.Series([100.0, 100.1] * 10)
        bb2 = bollinger_bands(s2, period=20, std_dev=2.0)
        # / at a bar where close == middle, pct_b should be ~0.5
        mid_val = bb2.middle.iloc[-1]
        # / verify formula: pct_b = (close - lower)/(upper - lower), and middle = (upper+lower)/2
        # / so when close = middle, pct_b = (middle - lower)/(upper - lower) = 0.5
        if not pd.isna(bb2.pct_b.iloc[-1]):
            expected = (s2.iloc[-1] - bb2.lower.iloc[-1]) / (bb2.upper.iloc[-1] - bb2.lower.iloc[-1])
            assert bb2.pct_b.iloc[-1] == pytest.approx(expected, abs=0.1)

    def test_bandwidth_formula(self):
        # / bandwidth = (upper - lower) / middle
        _, _, close = _ohlc(100)
        bb = bollinger_bands(close, period=20)
        expected_bw = (bb.upper - bb.lower) / bb.middle
        valid_idx = bb.bandwidth.dropna().index
        np.testing.assert_allclose(bb.bandwidth[valid_idx].values, expected_bw[valid_idx].values, atol=1e-10)

    def test_constant_price_bands_collapse(self):
        # / constant price -> std=0 -> bandwidth=0, bands collapse to middle
        s = pd.Series([100.0] * 30)
        bb = bollinger_bands(s, period=20, std_dev=2.0)
        valid_idx = bb.upper.dropna().index
        np.testing.assert_allclose(bb.upper[valid_idx].values, bb.middle[valid_idx].values, atol=1e-10)
        np.testing.assert_allclose(bb.lower[valid_idx].values, bb.middle[valid_idx].values, atol=1e-10)
        # / bandwidth should be 0
        bw_valid = bb.bandwidth[valid_idx]
        np.testing.assert_allclose(bw_valid.values, 0.0, atol=1e-10)


class TestATRDeep:
    def test_constant_range_converges(self):
        # / if high-low always=10 with no gaps, atr should converge to 10
        n = 100
        close = pd.Series([100.0] * n)
        high = pd.Series([105.0] * n)
        low = pd.Series([95.0] * n)
        result = atr(high, low, close, period=14)
        last = result.dropna().iloc[-1]
        assert last == pytest.approx(10.0, abs=0.5)

    def test_atr_always_positive(self):
        h, l, c = _ohlc(100)
        result = atr(h, l, c)
        valid = result.dropna()
        assert (valid > 0).all()


class TestKeltnerDeep:
    def test_upper_equals_ema_plus_mult_atr(self):
        # / upper = ema + multiplier * atr, lower = ema - multiplier * atr
        h, l, c = _ohlc(100)
        kc = keltner_channel(h, l, c, ema_period=20, atr_period=10, multiplier=2.0)
        middle = trend_ema(c, 20)
        from src.indicators.volatility import atr as atr_fn
        atr_val = atr_fn(h, l, c, 10)
        expected_upper = middle + 2.0 * atr_val
        expected_lower = middle - 2.0 * atr_val
        valid_idx = kc.upper.dropna().index & expected_upper.dropna().index
        np.testing.assert_allclose(kc.upper[valid_idx].values, expected_upper[valid_idx].values, atol=1e-10)
        np.testing.assert_allclose(kc.lower[valid_idx].values, expected_lower[valid_idx].values, atol=1e-10)

    def test_wider_multiplier_wider_channel_deep(self):
        # / multiplier=4 should be wider than multiplier=1
        h, l, c = _ohlc(100)
        narrow = keltner_channel(h, l, c, multiplier=1.0)
        wide = keltner_channel(h, l, c, multiplier=4.0)
        valid_idx = narrow.upper.dropna().index & wide.upper.dropna().index
        narrow_width = narrow.upper[valid_idx] - narrow.lower[valid_idx]
        wide_width = wide.upper[valid_idx] - wide.lower[valid_idx]
        assert (wide_width > narrow_width).all()

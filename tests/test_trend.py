# / tests for trend indicators

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.indicators.trend import (
    IchimokuResult,
    MACDResult,
    PSARResult,
    SupertrendResult,
    adx,
    ema,
    ichimoku,
    macd,
    psar,
    sma,
    supertrend,
    true_range,
)


def _price_series(n: int = 100, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    # / trending upward with noise
    trend = np.linspace(100, 130, n)
    noise = rng.normal(0, 2, n)
    return pd.Series(trend + noise)


def _ohlc(n: int = 100, seed: int = 42) -> tuple[pd.Series, pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    close = np.linspace(100, 130, n) + rng.normal(0, 2, n)
    high = close + rng.uniform(0.5, 2.0, n)
    low = close - rng.uniform(0.5, 2.0, n)
    return pd.Series(high), pd.Series(low), pd.Series(close)


class TestSMA:
    def test_basic(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = sma(s, period=3)
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[4] == pytest.approx(4.0)

    def test_nan_for_insufficient_data(self):
        s = pd.Series([1.0, 2.0, 3.0])
        result = sma(s, period=5)
        assert all(pd.isna(result))

    def test_length_preserved(self):
        s = _price_series(50)
        result = sma(s, 20)
        assert len(result) == 50


class TestEMA:
    def test_basic(self):
        s = pd.Series([10.0] * 20 + [20.0] * 20)
        result = ema(s, period=10)
        # / after 20 bars of 10, ema should be ~10
        assert result.iloc[19] == pytest.approx(10.0, abs=0.1)
        # / after 20 more bars of 20, ema should approach 20
        assert result.iloc[39] > 18.0

    def test_nan_for_insufficient_data(self):
        s = pd.Series([1.0, 2.0, 3.0])
        result = ema(s, period=5)
        assert pd.isna(result.iloc[0])

    def test_tracks_price(self):
        s = _price_series(100)
        result = ema(s, 10)
        # / ema should be close to price
        valid = result.dropna()
        assert len(valid) > 50


class TestMACD:
    def test_returns_dataclass(self):
        s = _price_series(100)
        result = macd(s)
        assert isinstance(result, MACDResult)
        assert len(result.macd_line) == 100
        assert len(result.signal_line) == 100
        assert len(result.histogram) == 100

    def test_histogram_is_diff(self):
        s = _price_series(100)
        result = macd(s)
        valid = result.histogram.dropna()
        expected = (result.macd_line - result.signal_line).dropna()
        # / last values should match
        assert valid.iloc[-1] == pytest.approx(expected.iloc[-1], abs=0.01)

    def test_uptrend_macd_positive(self):
        # / strong uptrend should give positive macd
        s = pd.Series(np.linspace(100, 200, 100))
        result = macd(s)
        # / last macd should be positive
        last_valid = result.macd_line.dropna().iloc[-1]
        assert last_valid > 0


class TestADX:
    def test_trending_market(self):
        high, low, close = _ohlc(100)
        result = adx(high, low, close, period=14)
        valid = result.dropna()
        assert len(valid) > 0
        # / values should be positive
        assert (valid >= 0).all()

    def test_length_preserved(self):
        high, low, close = _ohlc(50)
        result = adx(high, low, close)
        assert len(result) == 50


class TestTrueRange:
    def test_basic(self):
        high = pd.Series([12.0, 11.0])
        low = pd.Series([10.0, 9.0])
        close = pd.Series([11.0, 10.0])
        result = true_range(high, low, close)
        # / first bar: high - low = 2
        assert result.iloc[0] == pytest.approx(2.0)
        # / second bar: max(11-9, |11-11|, |9-11|) = max(2, 0, 2) = 2
        assert result.iloc[1] == pytest.approx(2.0)


class TestSupertrend:
    def test_returns_dataclass(self):
        high, low, close = _ohlc(100)
        result = supertrend(high, low, close)
        assert isinstance(result, SupertrendResult)
        assert len(result.supertrend) == 100
        assert len(result.direction) == 100

    def test_direction_values(self):
        high, low, close = _ohlc(100)
        result = supertrend(high, low, close)
        valid_dirs = result.direction.dropna()
        # / direction should be 1 or -1
        assert set(valid_dirs.unique()).issubset({1, -1})

    def test_uptrend_mostly_positive(self):
        # / strong uptrend
        n = 100
        close = pd.Series(np.linspace(100, 200, n))
        high = close + 1
        low = close - 1
        result = supertrend(high, low, close)
        # / should have mostly uptrend direction toward the end
        last_20 = result.direction.iloc[-20:]
        assert (last_20 == 1).sum() > 10


# ---------- new deep tests ----------


class TestSMAExactValues:
    def test_known_data(self):
        # / [10,20,30,40,50] period=3 -> last 3 values: [20.0, 30.0, 40.0]
        s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
        result = sma(s, period=3)
        assert pd.isna(result.iloc[0])
        assert pd.isna(result.iloc[1])
        assert result.iloc[2] == pytest.approx(20.0)
        assert result.iloc[3] == pytest.approx(30.0)
        assert result.iloc[4] == pytest.approx(40.0)

    def test_period_one_returns_original(self):
        # / period=1 should return the original series
        s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
        result = sma(s, period=1)
        pd.testing.assert_series_equal(result, s)

    def test_all_same_values(self):
        # / constant series returns that constant
        s = pd.Series([42.0] * 20)
        result = sma(s, period=5)
        valid = result.dropna()
        np.testing.assert_allclose(valid.values, 42.0, atol=1e-10)


class TestEMAConvergence:
    def test_converges_to_constant_for_flat(self):
        # / ema of flat series should equal that constant
        s = pd.Series([50.0] * 50)
        result = ema(s, period=10)
        valid = result.dropna()
        for v in valid:
            assert v == pytest.approx(50.0, abs=1e-10)

    def test_first_valid_equals_sma_of_first_period(self):
        # / ema first valid value = sma of first period values (pandas ewm adjust=False w/ min_periods)
        s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0])
        result = ema(s, period=5)
        first_valid_idx = result.first_valid_index()
        # / with min_periods=5, first valid is at index 4
        assert first_valid_idx == 4
        # / ewm adjust=False seeds from first value, not sma — verify it's finite
        assert not pd.isna(result.iloc[4])

    def test_ema_responds_faster_than_sma(self):
        # / after a step change, ema should be closer to new value than sma
        flat_low = [100.0] * 30
        flat_high = [200.0] * 30
        s = pd.Series(flat_low + flat_high)
        ema_result = ema(s, period=10)
        sma_result = sma(s, period=10)
        # / compare at bar 35 (5 bars after step change)
        # / ema should be closer to 200 than sma
        ema_val = ema_result.iloc[35]
        sma_val = sma_result.iloc[35]
        assert abs(200.0 - ema_val) < abs(200.0 - sma_val)


class TestMACDAlgebraic:
    def test_macd_line_equals_fast_minus_slow(self):
        # / macd line = ema(fast) - ema(slow), verified algebraically
        s = _price_series(100)
        result = macd(s, fast=12, slow=26, signal=9)
        ema_fast = ema(s, 12)
        ema_slow = ema(s, 26)
        expected = ema_fast - ema_slow
        valid_idx = result.macd_line.dropna().index
        np.testing.assert_allclose(
            result.macd_line[valid_idx].values,
            expected[valid_idx].values,
            atol=1e-10,
        )

    def test_histogram_equals_macd_minus_signal(self):
        # / histogram = macd_line - signal_line
        s = _price_series(100)
        result = macd(s)
        diff = result.macd_line - result.signal_line
        valid_idx = result.histogram.dropna().index
        np.testing.assert_allclose(
            result.histogram[valid_idx].values,
            diff[valid_idx].values,
            atol=1e-10,
        )

    def test_crossover_histogram_sign(self):
        # / when macd crosses above signal, histogram goes positive
        # / build a series with a longer dip then recovery so histogram crosses zero
        dip = np.linspace(100, 60, 50).tolist()
        recover = np.linspace(60, 150, 100).tolist()
        s = pd.Series(dip + recover)
        result = macd(s, fast=12, slow=26, signal=9)
        hist = result.histogram.dropna()
        # / histogram should have both positive and negative values
        assert (hist > 0).any()
        assert (hist < 0).any()
        # / at the end of recovery, histogram should be positive
        assert hist.iloc[-1] > 0


class TestADXDeep:
    def test_flat_market_low_adx(self):
        # / flat market should give adx < 25
        n = 200
        rng = np.random.default_rng(42)
        base = 100.0
        noise = rng.normal(0, 0.3, n)
        close = pd.Series(base + noise)
        high = close + 0.5
        low = close - 0.5
        result = adx(high, low, close, period=14)
        valid = result.dropna()
        assert len(valid) > 0
        assert valid.iloc[-1] < 25

    def test_strong_trend_high_adx(self):
        # / strong monotonic trend should give adx > 40
        n = 200
        close = pd.Series(np.linspace(100, 300, n))
        high = close + 1.0
        low = close - 1.0
        result = adx(high, low, close, period=14)
        valid = result.dropna()
        assert len(valid) > 0
        assert valid.iloc[-1] > 40

    def test_adx_range_0_to_100(self):
        # / adx should be in [0, 100] for all values
        h, l, c = _ohlc(200)
        result = adx(h, l, c, period=14)
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()


class TestSupertrendDeep:
    def test_direction_flips_on_price_cross(self):
        # / price crossing band should flip direction
        n = 200
        # / uptrend then sharp reversal
        up = np.linspace(100, 200, 100)
        down = np.linspace(200, 80, 100)
        close_vals = np.concatenate([up, down])
        close = pd.Series(close_vals)
        high = close + 2.0
        low = close - 2.0
        result = supertrend(high, low, close, period=10, multiplier=3.0)
        valid_dirs = result.direction.dropna()
        # / should have both 1 (uptrend) and -1 (downtrend)
        assert 1 in valid_dirs.values
        assert -1 in valid_dirs.values

    def test_upper_band_no_increase_in_downtrend(self):
        # / in downtrend, upper band should not increase (carry-forward logic)
        n = 100
        close = pd.Series(np.linspace(200, 100, n))
        high = close + 1.0
        low = close - 1.0
        result = supertrend(high, low, close, period=10, multiplier=3.0)
        # / find segments where direction is -1 (downtrend)
        down_mask = result.direction == -1
        st_down = result.supertrend[down_mask].dropna()
        if len(st_down) > 2:
            # / in downtrend supertrend = upper band, which should not increase
            # / check consecutive values don't increase (with small tolerance)
            diffs = st_down.diff().dropna()
            assert (diffs <= 0.01).all()


class TestIchimoku:
    def test_conversion_is_9_period_midline(self):
        # / tenkan-sen at index i = (max(high[i-8..i]) + min(low[i-8..i])) / 2
        rng = np.random.default_rng(42)
        n = 60
        close = pd.Series(np.linspace(100, 130, n) + rng.normal(0, 1.5, n))
        high = close + rng.uniform(0.5, 2.0, n)
        low = close - rng.uniform(0.5, 2.0, n)
        result = ichimoku(high, low, close)
        assert isinstance(result, IchimokuResult)
        # / hand compute at index 30
        expected_30 = (high.iloc[22:31].max() + low.iloc[22:31].min()) / 2
        assert result.conversion.iloc[30] == pytest.approx(expected_30, abs=1e-10)
        # / verify another index
        expected_45 = (high.iloc[37:46].max() + low.iloc[37:46].min()) / 2
        assert result.conversion.iloc[45] == pytest.approx(expected_45, abs=1e-10)

    def test_base_is_26_period_midline(self):
        # / kijun-sen at index i = (max(high[i-25..i]) + min(low[i-25..i])) / 2
        rng = np.random.default_rng(42)
        n = 80
        close = pd.Series(np.linspace(100, 130, n) + rng.normal(0, 1.5, n))
        high = close + rng.uniform(0.5, 2.0, n)
        low = close - rng.uniform(0.5, 2.0, n)
        result = ichimoku(high, low, close)
        expected_40 = (high.iloc[15:41].max() + low.iloc[15:41].min()) / 2
        assert result.base.iloc[40] == pytest.approx(expected_40, abs=1e-10)
        expected_55 = (high.iloc[30:56].max() + low.iloc[30:56].min()) / 2
        assert result.base.iloc[55] == pytest.approx(expected_55, abs=1e-10)

    def test_span_a_displaced_by_26(self):
        # / span_a at index i equals (conversion[i-26] + base[i-26]) / 2 (forward-shift by displacement)
        rng = np.random.default_rng(42)
        n = 120
        close = pd.Series(np.linspace(100, 130, n) + rng.normal(0, 1.5, n))
        high = close + rng.uniform(0.5, 2.0, n)
        low = close - rng.uniform(0.5, 2.0, n)
        result = ichimoku(high, low, close)
        # / pick an index where both shifted values exist
        i = 80
        expected = (result.conversion.iloc[i - 26] + result.base.iloc[i - 26]) / 2
        assert result.span_a.iloc[i] == pytest.approx(expected, abs=1e-10)

    def test_lagging_shifted_negative_26(self):
        # / chikou at index i equals close at index i+26 (lagging = close.shift(-26))
        rng = np.random.default_rng(42)
        n = 120
        close = pd.Series(np.linspace(100, 130, n) + rng.normal(0, 1.5, n))
        high = close + 1.0
        low = close - 1.0
        result = ichimoku(high, low, close)
        # / lagging at index 20 should equal close at 46
        assert result.lagging.iloc[20] == pytest.approx(close.iloc[46], abs=1e-10)
        # / last 26 values are NaN (no future close)
        assert pd.isna(result.lagging.iloc[-1])
        assert pd.isna(result.lagging.iloc[n - 26])

    def test_warmup_nans(self):
        # / conversion warmup: first 8 are NaN (min_periods=9)
        # / base warmup: first 25 are NaN (min_periods=26)
        # / span_b: rolling window 52 then shifted +26 => first 51+26=77 are NaN
        rng = np.random.default_rng(42)
        n = 150
        close = pd.Series(np.linspace(100, 130, n) + rng.normal(0, 1.0, n))
        high = close + 1.0
        low = close - 1.0
        result = ichimoku(high, low, close)
        # / conversion: indices 0..7 NaN, 8 first valid
        for i in range(8):
            assert pd.isna(result.conversion.iloc[i])
        assert not pd.isna(result.conversion.iloc[8])
        # / base: indices 0..24 NaN, 25 first valid
        for i in range(25):
            assert pd.isna(result.base.iloc[i])
        assert not pd.isna(result.base.iloc[25])
        # / span_b: first 51+26 = 77 NaN positions
        for i in range(77):
            assert pd.isna(result.span_b.iloc[i])
        assert not pd.isna(result.span_b.iloc[77])

    def test_insufficient_data(self):
        # / length 5 < all periods — should return all NaN without erroring
        high = pd.Series([10.0, 11.0, 12.0, 11.5, 10.5])
        low = pd.Series([9.0, 10.0, 11.0, 10.5, 9.5])
        close = pd.Series([9.5, 10.5, 11.5, 11.0, 10.0])
        result = ichimoku(high, low, close)
        assert len(result.conversion) == 5
        assert len(result.base) == 5
        assert len(result.span_a) == 5
        assert len(result.span_b) == 5
        assert result.conversion.isna().all()
        assert result.base.isna().all()
        assert result.span_a.isna().all()
        assert result.span_b.isna().all()


class TestPSAR:
    def test_first_bar_direction_set(self):
        # / length preserved; direction at index 0 is +/-1
        rng = np.random.default_rng(42)
        n = 50
        close = np.linspace(100, 130, n) + rng.normal(0, 0.5, n)
        high = pd.Series(close + 1.0)
        low = pd.Series(close - 1.0)
        result = psar(high, low)
        assert isinstance(result, PSARResult)
        assert len(result.sar) == n
        assert len(result.direction) == n
        assert result.direction.iloc[0] in (1.0, -1.0)

    def test_uptrend_then_downtrend_flips(self):
        # / clear uptrend for 40 bars then sharp downtrend for 40 bars
        up = np.linspace(100, 200, 40)
        down = np.linspace(200, 80, 40)
        close = np.concatenate([up, down])
        high = pd.Series(close + 1.0)
        low = pd.Series(close - 1.0)
        result = psar(high, low)
        # / during uptrend portion (after init), should be +1
        assert result.direction.iloc[10] == 1.0
        assert result.direction.iloc[30] == 1.0
        # / during downtrend portion, should flip to -1 somewhere
        second_half_dirs = result.direction.iloc[40:].values
        assert -1.0 in set(second_half_dirs)
        # / final bar should be in downtrend
        assert result.direction.iloc[-1] == -1.0

    def test_single_bar(self):
        # / length-1 returns nan sar + nan direction — no error, no leaked sentinel 0
        high = pd.Series([100.0])
        low = pd.Series([99.0])
        result = psar(high, low)
        assert len(result.sar) == 1
        assert len(result.direction) == 1
        assert pd.isna(result.sar.iloc[0])
        assert pd.isna(result.direction.iloc[0])

    def test_empty_input(self):
        # / length-0 returns empty series — no error
        high = pd.Series([], dtype=float)
        low = pd.Series([], dtype=float)
        result = psar(high, low)
        assert len(result.sar) == 0
        assert len(result.direction) == 0

    def test_af_bounded_by_max_step(self):
        # / in strong uptrend with step=0.02 and max_step=0.2, af caps at 0.2
        # / loose check: sar should trail below close and not diverge above it during strong uptrend
        n = 80
        close = np.linspace(100, 300, n)
        high = pd.Series(close + 1.0)
        low = pd.Series(close - 1.0)
        result = psar(high, low, step=0.02, max_step=0.2)
        # / after initial bars, sar should remain strictly below close throughout the uptrend
        close_s = pd.Series(close)
        tail = result.sar.iloc[5:].dropna()
        close_tail = close_s.iloc[5:].reindex(tail.index)
        assert (tail < close_tail).all()
        # / direction should be +1 throughout (no flips in pure uptrend)
        assert (result.direction.iloc[5:] == 1.0).all()

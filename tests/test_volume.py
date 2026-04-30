# / tests for volume indicators

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.indicators.volume import (
    VolumeProfile,
    mfi,
    obv,
    volume_profile,
    vwap,
)


def _ohlcv(n: int = 100, seed: int = 42):
    rng = np.random.default_rng(seed)
    close = np.linspace(100, 130, n) + rng.normal(0, 2, n)
    high = close + rng.uniform(0.5, 2.0, n)
    low = close - rng.uniform(0.5, 2.0, n)
    volume = rng.integers(100_000, 1_000_000, n).astype(float)
    return pd.Series(high), pd.Series(low), pd.Series(close), pd.Series(volume)


class TestOBV:
    def test_basic_up(self):
        close = pd.Series([10.0, 11.0, 12.0])
        volume = pd.Series([100.0, 200.0, 300.0])
        result = obv(close, volume)
        # / all up days: cumulative 0 + 200 + 300 = 500
        assert result.iloc[2] == 500.0

    def test_basic_down(self):
        close = pd.Series([12.0, 11.0, 10.0])
        volume = pd.Series([100.0, 200.0, 300.0])
        result = obv(close, volume)
        # / all down: 0 - 200 - 300 = -500
        assert result.iloc[2] == -500.0

    def test_flat_no_change(self):
        close = pd.Series([10.0, 10.0, 10.0])
        volume = pd.Series([100.0, 200.0, 300.0])
        result = obv(close, volume)
        assert result.iloc[2] == 0.0

    def test_length_preserved(self):
        _, _, close, volume = _ohlcv(50)
        result = obv(close, volume)
        assert len(result) == 50


class TestVWAP:
    def test_basic(self):
        high = pd.Series([11.0, 12.0])
        low = pd.Series([9.0, 10.0])
        close = pd.Series([10.0, 11.0])
        volume = pd.Series([100.0, 100.0])
        result = vwap(high, low, close, volume)
        # / tp = (11+9+10)/3=10, (12+10+11)/3=11
        # / vwap = (10*100 + 11*100) / 200 = 10.5
        assert result.iloc[1] == pytest.approx(10.5)

    def test_higher_volume_pulls_vwap(self):
        high = pd.Series([11.0, 12.0])
        low = pd.Series([9.0, 10.0])
        close = pd.Series([10.0, 11.0])
        # / second bar has 10x volume
        volume = pd.Series([100.0, 1000.0])
        result = vwap(high, low, close, volume)
        # / vwap should be closer to bar 2's tp (11) than bar 1's tp (10)
        assert result.iloc[1] > 10.5


class TestVolumeProfile:
    def test_returns_dataclass(self):
        _, _, close, volume = _ohlcv(100)
        result = volume_profile(close, volume)
        assert isinstance(result, VolumeProfile)

    def test_poc_in_range(self):
        _, _, close, volume = _ohlcv(100)
        result = volume_profile(close, volume)
        assert result.poc >= close.min()
        assert result.poc <= close.max()

    def test_value_area(self):
        _, _, close, volume = _ohlcv(100)
        result = volume_profile(close, volume)
        assert result.value_area_low <= result.poc
        assert result.value_area_high >= result.poc

    def test_empty_series(self):
        close = pd.Series(dtype=float)
        volume = pd.Series(dtype=float)
        result = volume_profile(close, volume)
        assert result.poc == 0.0

    def test_single_price(self):
        close = pd.Series([100.0] * 10)
        volume = pd.Series([1000.0] * 10)
        result = volume_profile(close, volume)
        assert result.poc == pytest.approx(100.0)


class TestMFI:
    def test_range_0_to_100(self):
        h, l, c, v = _ohlcv(100)
        result = mfi(h, l, c, v)
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_uptrend_high_mfi(self):
        # / strong uptrend with noise, enough points for wilder smoothing
        rng = np.random.default_rng(42)
        n = 100
        close = pd.Series(np.linspace(100, 200, n) + rng.normal(0, 0.5, n))
        high = close + 1
        low = close - 1
        volume = pd.Series([1_000_000.0] * n)
        result = mfi(high, low, close, volume, period=14)
        last = result.dropna().iloc[-1]
        assert last > 50

    def test_length_preserved(self):
        h, l, c, v = _ohlcv(50)
        result = mfi(h, l, c, v)
        assert len(result) == 50


# ---------- new deep tests ----------


class TestOBVExact:
    def test_exact_cumsum(self):
        # / close=[10,12,11,13], vol=[100,200,300,400]
        # / directions: nan, +1, -1, +1
        # / obv: 0*100, +1*200, -1*300, +1*400 -> cumsum: 0, 200, -100, 300
        close = pd.Series([10.0, 12.0, 11.0, 13.0])
        vol = pd.Series([100.0, 200.0, 300.0, 400.0])
        result = obv(close, vol)
        # / first bar: direction from shift(1) is nan -> np.where gives -1 (not > prev)
        # / actually: close[0] > close.shift(1)[0]=nan -> False, close[0] < nan -> False -> direction=0
        # / so obv = cumsum of [0*100, 1*200, -1*300, 1*400] = [0, 200, -100, 300]
        assert result.iloc[0] == pytest.approx(0.0)
        assert result.iloc[1] == pytest.approx(200.0)
        assert result.iloc[2] == pytest.approx(-100.0)
        assert result.iloc[3] == pytest.approx(300.0)


class TestVWAPDeep:
    def test_equal_volumes_average_tp(self):
        # / with equal volumes, vwap = cumulative average of typical prices
        n = 5
        high = pd.Series([12.0, 13.0, 14.0, 15.0, 16.0])
        low = pd.Series([8.0, 9.0, 10.0, 11.0, 12.0])
        close = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])
        vol = pd.Series([100.0] * n)
        result = vwap(high, low, close, vol)
        # / tp = (h+l+c)/3
        tp = (high + low + close) / 3
        # / with equal volumes, vwap at each bar = cumulative mean of tp
        for i in range(n):
            expected = tp.iloc[:i + 1].mean()
            assert result.iloc[i] == pytest.approx(expected, abs=0.01)

    def test_vwap_between_low_and_high(self):
        # / vwap should be between overall min low and max high
        h, l, c, v = _ohlcv(100)
        result = vwap(h, l, c, v)
        valid = result.dropna()
        assert (valid >= l.min()).all()
        assert (valid <= h.max()).all()

    def test_session_reset_with_dates(self):
        # / vwap resets at each date boundary when dates param provided
        high = pd.Series([11.0, 12.0, 13.0, 14.0])
        low = pd.Series([9.0, 10.0, 11.0, 12.0])
        close = pd.Series([10.0, 11.0, 12.0, 13.0])
        vol = pd.Series([100.0, 100.0, 100.0, 100.0])
        dates = pd.Series(["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02"])
        result = vwap(high, low, close, vol, dates=dates)
        # / bar 2 (idx 1): cumulative vwap of day 1 = avg tp of bars 0-1
        tp0, tp1 = (11+9+10)/3, (12+10+11)/3
        assert result.iloc[1] == pytest.approx((tp0*100 + tp1*100) / 200, abs=0.01)
        # / bar 3 (idx 2): session reset, vwap = tp of bar 2 only
        tp2 = (13+11+12)/3
        assert result.iloc[2] == pytest.approx(tp2, abs=0.01)

    def test_no_dates_unchanged_behavior(self):
        # / without dates param, behaves same as before (cumsum across all)
        h, l, c, v = _ohlcv(50)
        result_no_dates = vwap(h, l, c, v)
        result_none = vwap(h, l, c, v, dates=None)
        pd.testing.assert_series_equal(result_no_dates, result_none)


class TestVolumeProfileDeep:
    def test_poc_is_highest_volume(self):
        # / poc should be at the price level with highest volume
        # / concentrate volume at price 150
        close = pd.Series([100.0, 110.0, 150.0, 150.0, 150.0, 150.0, 200.0])
        vol = pd.Series([100.0, 100.0, 10000.0, 10000.0, 10000.0, 10000.0, 100.0])
        result = volume_profile(close, vol, num_bins=10)
        # / poc should be near 150
        assert abs(result.poc - 150.0) < 15.0

    def test_value_area_contains_70pct(self):
        # / value area should contain ~70% of total volume
        _, _, close, vol = _ohlcv(100)
        result = volume_profile(close, vol, num_bins=50, value_area_pct=0.70)
        total_vol = vol.sum()
        # / sum volume in bins between value_area_low and value_area_high
        bins = np.linspace(close.min(), close.max(), 51)
        bin_centers = (bins[:-1] + bins[1:]) / 2
        bin_indices = np.digitize(close.values, bins) - 1
        bin_indices = np.clip(bin_indices, 0, 49)
        vol_at_price = np.zeros(50)
        for i, idx in enumerate(bin_indices):
            vol_at_price[idx] += vol.iloc[i]
        # / sum volume in value area
        va_mask = (bin_centers >= result.value_area_low) & (bin_centers <= result.value_area_high)
        va_vol = vol_at_price[va_mask].sum()
        assert va_vol / total_vol >= 0.65  # / allow some tolerance

    def test_num_bins_parameter(self):
        # / num_bins should control the number of bins
        _, _, close, vol = _ohlcv(100)
        result_20 = volume_profile(close, vol, num_bins=20)
        result_100 = volume_profile(close, vol, num_bins=100)
        assert len(result_20.price_levels) == 20
        assert len(result_100.price_levels) == 100


class TestMFIDeep:
    def test_mfi_range(self):
        h, l, c, v = _ohlcv(100)
        result = mfi(h, l, c, v, period=14)
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_all_positive_flow_near_100(self):
        # / steep uptrend with noise to ensure some negative diffs exist
        rng = np.random.default_rng(42)
        n = 100
        close = pd.Series(np.linspace(100, 300, n) + rng.normal(0, 1.5, n))
        high = close + 1.0
        low = close - 1.0
        vol = pd.Series([1_000_000.0] * n)
        result = mfi(high, low, close, vol, period=14)
        last = result.dropna().iloc[-1]
        assert last > 90

    def test_all_negative_flow_near_0(self):
        # / steep downtrend with noise to ensure some positive diffs exist
        rng = np.random.default_rng(42)
        n = 100
        close = pd.Series(np.linspace(300, 100, n) + rng.normal(0, 1.5, n))
        high = close + 1.0
        low = close - 1.0
        vol = pd.Series([1_000_000.0] * n)
        result = mfi(high, low, close, vol, period=14)
        last = result.dropna().iloc[-1]
        assert last < 10

    def test_equal_flow_near_50(self):
        # / alternating up/down -> roughly equal flow -> mfi near 50
        rng = np.random.default_rng(42)
        n = 100
        base = 100.0
        noise = rng.normal(0, 1, n)
        close = pd.Series(base + noise)
        high = close + 1.0
        low = close - 1.0
        vol = pd.Series([1_000_000.0] * n)
        result = mfi(high, low, close, vol, period=14)
        last = result.dropna().iloc[-1]
        assert 20 < last < 80

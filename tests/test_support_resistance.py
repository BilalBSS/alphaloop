# / tests for support/resistance indicators

import numpy as np
import pandas as pd
import pytest

from src.indicators.support_resistance import (
    fib_auto,
    fibonacci_retracement,
    pivot_points,
    sr_zones,
    sr_zones_series,
)


def _make_series(values):
    return pd.Series(values, dtype=float)


class TestPivotPoints:
    def test_standard_hand_computed(self):
        # / H=110, L=90, C=100
        pp = pivot_points(110, 90, 100, method="standard")
        assert pp.pivot == pytest.approx(100.0)       # (110+90+100)/3
        assert pp.r1 == pytest.approx(110.0)           # 2*100 - 90
        assert pp.s1 == pytest.approx(90.0)             # 2*100 - 110
        assert pp.r2 == pytest.approx(120.0)            # 100 + (110-90)
        assert pp.s2 == pytest.approx(80.0)             # 100 - (110-90)
        assert pp.r3 == pytest.approx(130.0)            # 110 + 2*(100-90)
        assert pp.s3 == pytest.approx(70.0)             # 90 - 2*(110-100)

    def test_fibonacci_hand_computed(self):
        pp = pivot_points(110, 90, 100, method="fibonacci")
        assert pp.pivot == pytest.approx(100.0)
        assert pp.r1 == pytest.approx(100 + 0.382 * 20)  # 107.64
        assert pp.s1 == pytest.approx(100 - 0.382 * 20)  # 92.36
        assert pp.r2 == pytest.approx(100 + 0.618 * 20)  # 112.36
        assert pp.s2 == pytest.approx(100 - 0.618 * 20)  # 87.64

    def test_woodie_hand_computed(self):
        pp = pivot_points(110, 90, 100, method="woodie")
        # / woodie pivot = (H+L+2C)/4 = (110+90+200)/4 = 100
        assert pp.pivot == pytest.approx(100.0)
        assert pp.r1 == pytest.approx(110.0)   # 2*100 - 90
        assert pp.s1 == pytest.approx(90.0)     # 2*100 - 110

    def test_symmetry(self):
        # / with equal distances, R and S levels should be symmetric
        pp = pivot_points(110, 90, 100)
        assert pp.r1 - pp.pivot == pytest.approx(pp.pivot - pp.s1)
        assert pp.r2 - pp.pivot == pytest.approx(pp.pivot - pp.s2)

    def test_r_above_s(self):
        pp = pivot_points(150, 100, 120)
        assert pp.r1 > pp.s1
        assert pp.r2 > pp.s2
        assert pp.r3 > pp.s3


class TestFibonacciRetracement:
    def test_known_levels(self):
        # / swing high=200, swing low=100, diff=100
        high = _make_series([180, 190, 200, 195, 185])
        low = _make_series([120, 110, 100, 105, 110])
        fib = fibonacci_retracement(high, low, lookback=5)
        assert fib.swing_high == 200
        assert fib.swing_low == 100
        assert fib.level_236 == pytest.approx(200 - 23.6)
        assert fib.level_382 == pytest.approx(200 - 38.2)
        assert fib.level_500 == pytest.approx(150.0)
        assert fib.level_618 == pytest.approx(200 - 61.8)
        assert fib.level_786 == pytest.approx(200 - 78.6)

    def test_levels_are_ordered(self):
        high = _make_series([150, 160, 170])
        low = _make_series([100, 105, 110])
        fib = fibonacci_retracement(high, low, lookback=3)
        assert fib.level_236 > fib.level_382
        assert fib.level_382 > fib.level_500
        assert fib.level_500 > fib.level_618
        assert fib.level_618 > fib.level_786

    def test_flat_data(self):
        # / swing_high == swing_low
        high = _make_series([100, 100, 100])
        low = _make_series([100, 100, 100])
        fib = fibonacci_retracement(high, low)
        assert fib.level_236 == fib.level_618  # all equal
        assert fib.level_500 == 100.0

    def test_respects_lookback(self):
        high = _make_series([200, 150, 160, 170, 180])
        low = _make_series([50, 140, 150, 160, 170])
        # / lookback=3 should only see last 3 bars
        fib = fibonacci_retracement(high, low, lookback=3)
        assert fib.swing_high == 180  # max of last 3 highs
        assert fib.swing_low == 150   # min of last 3 lows


class TestSRZones:
    def test_finds_zones_at_clusters(self):
        # / prices cluster around 100 and 110
        close = _make_series([100, 101, 100, 99, 110, 111, 110, 109, 105])
        high = close + 1
        low = close - 1
        zones = sr_zones(close, high, low, num_zones=3, tolerance_pct=0.03)
        assert len(zones) >= 1
        levels = [z.level for z in zones]
        # / should find zones near 100 and 110
        assert any(abs(l - 100) < 5 for l in levels) or any(abs(l - 110) < 5 for l in levels)

    def test_zone_strength(self):
        # / more touches = higher strength
        close = _make_series([100, 100, 100, 100, 100, 200])
        high = close + 1
        low = close - 1
        zones = sr_zones(close, high, low, num_zones=2, tolerance_pct=0.02)
        # / zone near 100 should have more strength
        if zones:
            assert zones[0].strength >= 2

    def test_empty_on_insufficient_data(self):
        close = _make_series([100])
        high = _make_series([101])
        low = _make_series([99])
        zones = sr_zones(close, high, low)
        assert len(zones) == 0

    def test_support_resistance_classification(self):
        # / current price at 105, zone at 100 = support, zone at 110 = resistance
        close = _make_series([100, 100, 110, 110, 105])
        high = close + 1
        low = close - 1
        zones = sr_zones(close, high, low, num_zones=5, tolerance_pct=0.03)
        types = {z.type for z in zones}
        # / should have at least support or resistance
        assert len(types) >= 1


class TestSRZonesSeries:
    def test_returns_series_same_length(self):
        close = _make_series([100, 102, 104, 106, 108])
        high = close + 1
        low = close - 1
        result = sr_zones_series(close, high, low)
        assert len(result) == len(close)

    def test_zero_when_no_zones(self):
        close = _make_series([100])
        high = _make_series([101])
        low = _make_series([99])
        result = sr_zones_series(close, high, low)
        assert result.iloc[0] == 0.0

    def test_sign_indicates_direction(self):
        # / price above zone = positive, below = negative
        close = _make_series([100, 100, 100, 100, 120])
        high = close + 1
        low = close - 1
        result = sr_zones_series(close, high, low, num_zones=2)
        # / last bar at 120 should be positive (above the 100 zone)
        if result.iloc[-1] != 0:
            assert result.iloc[-1] > 0


class TestFibAuto:
    def test_fib_auto_matches_fib_retracement_when_lookback_leq_len(self):
        # / when lookback == len, fib_auto should equal fibonacci_retracement exactly
        rng = np.random.default_rng(42)
        n = 100
        close = _make_series(100 + rng.normal(0, 5, n))
        high = close + rng.uniform(0.5, 2.0, n)
        low = close - rng.uniform(0.5, 2.0, n)
        auto = fib_auto(high, low, close, lookback=100)
        direct = fibonacci_retracement(high, low, 100)
        assert auto.swing_high == pytest.approx(direct.swing_high, abs=1e-10)
        assert auto.swing_low == pytest.approx(direct.swing_low, abs=1e-10)
        assert auto.level_236 == pytest.approx(direct.level_236, abs=1e-10)
        assert auto.level_382 == pytest.approx(direct.level_382, abs=1e-10)
        assert auto.level_500 == pytest.approx(direct.level_500, abs=1e-10)
        assert auto.level_618 == pytest.approx(direct.level_618, abs=1e-10)
        assert auto.level_786 == pytest.approx(direct.level_786, abs=1e-10)

    def test_fib_auto_clamps_lookback_when_too_long(self):
        # / lookback larger than series length should clamp to len(close)
        rng = np.random.default_rng(42)
        n = 30
        close = _make_series(100 + rng.normal(0, 5, n))
        high = close + 1.0
        low = close - 1.0
        auto = fib_auto(high, low, close, lookback=100)
        direct = fibonacci_retracement(high, low, 30)
        assert auto.swing_high == pytest.approx(direct.swing_high, abs=1e-10)
        assert auto.swing_low == pytest.approx(direct.swing_low, abs=1e-10)
        assert auto.level_500 == pytest.approx(direct.level_500, abs=1e-10)

    def test_fib_auto_swing_high_low_correct(self):
        # / with known max=150, min=100, verify swing_high and swing_low
        high = _make_series([120, 130, 150, 140, 135])
        low = _make_series([105, 110, 125, 115, 100])
        close = _make_series([115, 125, 140, 125, 115])
        auto = fib_auto(high, low, close, lookback=5)
        assert auto.swing_high == 150.0
        assert auto.swing_low == 100.0
        # / level_500 should be midpoint
        assert auto.level_500 == pytest.approx(125.0)

    def test_fib_auto_flat_data(self):
        # / constant price => no div-by-zero, all levels == swing_high
        high = _make_series([100.0] * 20)
        low = _make_series([100.0] * 20)
        close = _make_series([100.0] * 20)
        auto = fib_auto(high, low, close, lookback=100)
        assert auto.swing_high == 100.0
        assert auto.swing_low == 100.0
        assert auto.level_236 == 100.0
        assert auto.level_382 == 100.0
        assert auto.level_500 == 100.0
        assert auto.level_618 == 100.0
        assert auto.level_786 == 100.0

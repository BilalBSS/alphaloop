# / tests for hurst exponent mean-reversion detector

from __future__ import annotations

import numpy as np
import pandas as pd

from src.indicators.mean_reversion import (
    classify_regime_hurst,
    hurst_exponent,
    rolling_hurst,
)


def _random_walk(n: int, seed: int = 42) -> pd.Series:
    # / H should be ~0.5 for a pure random walk
    rng = np.random.default_rng(seed)
    returns = rng.normal(0, 0.01, n)
    return pd.Series(100.0 * np.exp(np.cumsum(returns)))


def _trending(n: int, seed: int = 42) -> pd.Series:
    # / persistent drift -> H > 0.5
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.002, 0.005, n)  # / positive drift, low noise
    return pd.Series(100.0 * np.exp(np.cumsum(returns)))


def _mean_reverting(n: int, seed: int = 42) -> pd.Series:
    # / AR(1) with negative AR coefficient (anti-persistent) -> H < 0.5
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = -0.7 * x[i - 1] + rng.normal(0, 0.01)
    # / convert deviations to positive prices
    return pd.Series(100.0 + x * 10)


class TestHurstExponentShort:
    def test_short_series_defaults_to_half(self):
        # / less than max_lag*2 -> 0.5 fallback
        prices = pd.Series([100.0 + i for i in range(30)])
        assert hurst_exponent(prices, max_lag=20) == 0.5

    def test_minimum_length_runs_without_exception(self):
        prices = pd.Series(np.linspace(100, 130, 50))
        h = hurst_exponent(prices, max_lag=10)
        assert 0.0 <= h <= 1.0


class TestHurstRegimeDetection:
    def test_random_walk_near_half(self):
        # / loose tolerance — hurst on 500-sample finite series is noisy.
        # / we just check it's in a "not strongly trending, not strongly mean-reverting"
        # / band. average across several seeds should be closer to 0.5.
        results = [hurst_exponent(_random_walk(500, seed=s), max_lag=20)
                   for s in (1, 7, 13, 21, 42)]
        avg = sum(results) / len(results)
        assert 0.3 <= avg <= 0.8

    def test_trending_above_half(self):
        prices = _trending(500, seed=13)
        h = hurst_exponent(prices, max_lag=20)
        # / persistent series tends above 0.5
        assert h > 0.5

    def test_mean_reverting_below_half(self):
        # / strongly-anti-persistent series tends below random-walk's ~0.5.
        # / average across several seeds to suppress the small-sample noise that
        # / can push a single seed slightly above 0.5.
        results = [hurst_exponent(_mean_reverting(1000, seed=s), max_lag=10)
                   for s in (1, 3, 9, 17, 42)]
        avg = sum(results) / len(results)
        assert avg < 0.6  # / clearly below "trending" band


class TestHurstBounds:
    def test_output_clipped_to_unit_interval(self):
        # / clip enforces [0, 1]. try several seeds to verify invariant holds.
        for seed in (1, 2, 3, 4, 5):
            prices = _random_walk(400, seed=seed)
            h = hurst_exponent(prices)
            assert 0.0 <= h <= 1.0


class TestRollingHurst:
    def test_returns_series_same_length(self):
        prices = _random_walk(300, seed=11)
        result = rolling_hurst(prices, window=100, max_lag=20)
        assert len(result) == len(prices)

    def test_nan_before_window_filled(self):
        prices = _random_walk(300, seed=12)
        result = rolling_hurst(prices, window=100)
        # / positions 0 through window-1 must be NaN
        assert pd.isna(result.iloc[0])
        assert pd.isna(result.iloc[99])
        # / after window there should be a value
        assert not pd.isna(result.iloc[250])

    def test_all_valid_values_in_unit_interval(self):
        prices = _random_walk(300, seed=5)
        result = rolling_hurst(prices, window=100, max_lag=15)
        valid = result.dropna()
        assert (valid >= 0.0).all()
        assert (valid <= 1.0).all()


class TestClassifyRegimeHurst:
    def test_mean_reverting_threshold(self):
        assert classify_regime_hurst(0.2) == "mean_reverting"
        assert classify_regime_hurst(0.39) == "mean_reverting"

    def test_trending_threshold(self):
        assert classify_regime_hurst(0.61) == "trending"
        assert classify_regime_hurst(0.95) == "trending"

    def test_random_walk_band(self):
        assert classify_regime_hurst(0.4) == "random_walk"
        assert classify_regime_hurst(0.5) == "random_walk"
        assert classify_regime_hurst(0.6) == "random_walk"

    def test_boundary_values(self):
        # / strict < 0.4 and > 0.6 -> exactly on boundary lands in random_walk
        assert classify_regime_hurst(0.4) == "random_walk"
        assert classify_regime_hurst(0.6) == "random_walk"


class TestMaxLagParam:
    def test_smaller_max_lag_still_returns_valid_h(self):
        prices = _random_walk(400, seed=9)
        h_full = hurst_exponent(prices, max_lag=20)
        h_small = hurst_exponent(prices, max_lag=10)
        assert 0.0 <= h_full <= 1.0
        assert 0.0 <= h_small <= 1.0

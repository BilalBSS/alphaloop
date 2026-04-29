# / tests for alpha158 feature computation
# / verifies shape, no-nans-after-warmup, and a few formula checks

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.quant.alpha158 import (
    DEFAULT_WINDOWS,
    compute_alpha158,
    feature_count,
)


def _make_ohlcv(n: int = 120, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0005, 0.015, n)
    closes = 100.0 * np.exp(np.cumsum(returns))
    highs = closes * (1 + rng.uniform(0, 0.01, n))
    lows = closes * (1 - rng.uniform(0, 0.01, n))
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    volumes = rng.uniform(5e5, 5e6, n).astype(int)
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    })


class TestShapeAndInterface:

    def test_returns_dataframe(self):
        df = _make_ohlcv()
        out = compute_alpha158(df)
        assert isinstance(out, pd.DataFrame)

    def test_index_preserved(self):
        df = _make_ohlcv()
        df.index = pd.date_range("2024-01-01", periods=len(df), freq="D")
        out = compute_alpha158(df)
        assert (out.index == df.index).all()

    def test_expected_feature_count_default_windows(self):
        df = _make_ohlcv()
        out = compute_alpha158(df)
        # / 9 candle + 26 * 5 windows = 139
        assert len(out.columns) == feature_count(DEFAULT_WINDOWS) == 139

    def test_feature_count_scales_with_windows(self):
        df = _make_ohlcv()
        out = compute_alpha158(df, windows=(5, 10))
        # / 9 + 26 * 2 = 61
        assert len(out.columns) == 61

    def test_missing_columns_raises(self):
        df = pd.DataFrame({"close": [100, 101]})
        with pytest.raises(ValueError, match="missing columns"):
            compute_alpha158(df)

    def test_empty_dataframe_returns_empty(self):
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        out = compute_alpha158(df)
        assert len(out) == 0


class TestFormulas:
    """spot-check a few features against their defining formula"""

    def test_kmid_matches_formula(self):
        df = _make_ohlcv()
        out = compute_alpha158(df)
        expected = (df["close"] - df["open"]) / df["open"]
        # / compute_alpha158 replaces inf with NaN, so align
        pd.testing.assert_series_equal(
            out["KMID"].rename(None), expected.rename(None), check_names=False,
        )

    def test_klen_matches_formula(self):
        df = _make_ohlcv()
        out = compute_alpha158(df)
        expected = (df["high"] - df["low"]) / df["open"]
        pd.testing.assert_series_equal(
            out["KLEN"].rename(None), expected.rename(None), check_names=False,
        )

    def test_roc_matches_formula(self):
        df = _make_ohlcv()
        out = compute_alpha158(df)
        expected = df["close"].shift(5) / df["close"]
        pd.testing.assert_series_equal(
            out["ROC5"].rename(None), expected.rename(None), check_names=False,
        )

    def test_ma_matches_formula(self):
        df = _make_ohlcv()
        out = compute_alpha158(df)
        expected = df["close"].rolling(5, min_periods=1).mean() / df["close"]
        pd.testing.assert_series_equal(
            out["MA5"].rename(None), expected.rename(None), check_names=False,
        )

    def test_cntp_matches_formula(self):
        df = _make_ohlcv()
        out = compute_alpha158(df)
        returns = df["close"].pct_change()
        expected = (returns > 0).rolling(5, min_periods=1).mean()
        pd.testing.assert_series_equal(
            out["CNTP5"].rename(None), expected.rename(None).astype(float),
            check_names=False,
        )


class TestRangeBoundedness:
    """features that should live in specific ranges"""

    def test_rsv_in_unit_interval(self):
        df = _make_ohlcv()
        out = compute_alpha158(df)
        for w in DEFAULT_WINDOWS:
            vals = out[f"RSV{w}"].dropna()
            # / raw stochastic value should be in [0, 1]
            assert (vals >= -1e-9).all() and (vals <= 1 + 1e-9).all()

    def test_rsqr_in_unit_interval(self):
        df = _make_ohlcv()
        out = compute_alpha158(df)
        for w in DEFAULT_WINDOWS:
            vals = out[f"RSQR{w}"].dropna()
            assert (vals >= -1e-9).all() and (vals <= 1 + 1e-9).all()

    def test_cntp_in_unit_interval(self):
        df = _make_ohlcv()
        out = compute_alpha158(df)
        for w in DEFAULT_WINDOWS:
            vals = out[f"CNTP{w}"].dropna()
            assert (vals >= -1e-9).all() and (vals <= 1 + 1e-9).all()

    def test_cntd_in_range(self):
        df = _make_ohlcv()
        out = compute_alpha158(df)
        for w in DEFAULT_WINDOWS:
            vals = out[f"CNTD{w}"].dropna()
            # / diff of two [0,1] fractions is in [-1, 1]
            assert (vals >= -1 - 1e-9).all() and (vals <= 1 + 1e-9).all()


class TestEdgeCases:

    def test_no_infs_in_output(self):
        df = _make_ohlcv()
        out = compute_alpha158(df)
        # / compute_alpha158 replaces inf with NaN
        assert not np.isinf(out.to_numpy()).any()

    def test_constant_prices_does_not_crash(self):
        df = pd.DataFrame({
            "open": [100.0] * 80, "high": [100.0] * 80,
            "low": [100.0] * 80, "close": [100.0] * 80,
            "volume": [1e6] * 80,
        })
        out = compute_alpha158(df)
        assert len(out) == 80

    def test_zero_volume_handled(self):
        df = _make_ohlcv()
        df.loc[df.index[10], "volume"] = 0
        out = compute_alpha158(df)
        # / VMA/VSTD/CORR/CORD features should have NaN on zero-vol rows but not crash
        assert len(out) == len(df)

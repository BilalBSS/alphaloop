# / phase 6 step 9: feature-set switch + alpha158 benchmark tests

from __future__ import annotations

import os
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.quant import ml_signals


def _synthetic_ohlcv(n: int = 300, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0005, 0.01, size=n)
    close = 100 * np.cumprod(1 + ret)
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    open_ = close * (1 + rng.normal(0, 0.002, n))
    vol = rng.integers(1_000_000, 10_000_000, n)
    idx = pd.date_range("2023-01-01", periods=n)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": vol,
    }, index=idx)


class TestFeatureSetSelector:
    def test_default_is_handbuilt(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ML_FEATURE_SET", None)
            assert ml_signals._feature_set() == "handbuilt"

    def test_env_override_alpha158(self):
        with patch.dict(os.environ, {"ML_FEATURE_SET": "alpha158"}):
            assert ml_signals._feature_set() == "alpha158"

    def test_env_override_both(self):
        with patch.dict(os.environ, {"ML_FEATURE_SET": "BOTH"}):
            assert ml_signals._feature_set() == "both"

    def test_invalid_env_falls_back_to_handbuilt(self):
        with patch.dict(os.environ, {"ML_FEATURE_SET": "sentient-ai"}):
            assert ml_signals._feature_set() == "handbuilt"


class TestBuildFeaturesFeatureSet:
    def test_handbuilt_only_has_no_alpha_prefix_cols(self):
        ohlcv = _synthetic_ohlcv(150)
        feats = ml_signals.build_features(ohlcv, feature_set="handbuilt")
        assert any(c.startswith("rsi_") or c.startswith("macd") for c in feats.columns)
        assert not any(c.startswith("a158_") for c in feats.columns)

    def test_alpha158_only_has_a158_prefix_cols(self):
        ohlcv = _synthetic_ohlcv(150)
        feats = ml_signals.build_features(ohlcv, feature_set="alpha158")
        assert any(c.startswith("a158_") for c in feats.columns)
        # / alpha-only mode does not include the handbuilt rsi/macd
        assert not any(c == "rsi_14" for c in feats.columns)
        assert not any(c.startswith("macd_hist") for c in feats.columns)

    def test_both_includes_both(self):
        ohlcv = _synthetic_ohlcv(200)
        feats = ml_signals.build_features(ohlcv, feature_set="both")
        assert any(c.startswith("a158_") for c in feats.columns)
        assert any(c == "rsi_14" for c in feats.columns)

    def test_unknown_feature_set_falls_back_silently(self):
        ohlcv = _synthetic_ohlcv(150)
        # / unknown feature_set name triggers _feature_set() env path → "handbuilt"
        with patch.dict(os.environ, {"ML_FEATURE_SET": "mystery"}):
            feats = ml_signals.build_features(ohlcv, feature_set="mystery")
            assert any(c == "rsi_14" for c in feats.columns)


class TestBenchmarkFeatureSets:
    @pytest.mark.asyncio
    async def test_short_history_returns_insufficient_error(self):
        pytest.importorskip("lightgbm")
        pytest.importorskip("sklearn")
        ohlcv = _synthetic_ohlcv(80)
        result = await ml_signals.benchmark_feature_sets(ohlcv)
        for key in ("handbuilt", "alpha158"):
            assert result[key].get("error") in ("insufficient_history", "insufficient_after_split")

    @pytest.mark.asyncio
    async def test_full_history_returns_brier_for_both_sets(self):
        pytest.importorskip("lightgbm")
        pytest.importorskip("sklearn")
        ohlcv = _synthetic_ohlcv(400)
        result = await ml_signals.benchmark_feature_sets(ohlcv)
        assert "brier" in result["handbuilt"]
        assert "brier" in result["alpha158"]
        # / winner should match the lower brier
        assert result["winner"] in ("handbuilt", "alpha158")
        lower = min(result["handbuilt"]["brier"], result["alpha158"]["brier"])
        winner_set = result["winner"]
        assert result[winner_set]["brier"] == lower

    @pytest.mark.asyncio
    async def test_missing_lightgbm_returns_error(self):
        try:
            import lightgbm  # noqa: F401
            pytest.skip("lightgbm is installed; this test only runs when it's missing")
        except ImportError:
            pass
        ohlcv = _synthetic_ohlcv(400)
        result = await ml_signals.benchmark_feature_sets(ohlcv)
        assert result == {"error": "lightgbm_not_installed"}

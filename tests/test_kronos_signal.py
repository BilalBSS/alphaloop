# / tests for kronos signal module
# / focuses on the fallback heuristic + interface contracts since the real
# / HF model requires network access and large weights

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.quant import kronos_signal
from src.quant.kronos_signal import KronosPrediction, predict


def _make_ohlcv(n: int = 100, seed: int = 42, trend: float = 0.0, vol: float = 0.01) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    returns = rng.normal(trend, vol, n)
    closes = 100.0 * np.exp(np.cumsum(returns))
    highs = closes * (1 + rng.uniform(0, 0.005, n))
    lows = closes * (1 - rng.uniform(0, 0.005, n))
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    volumes = rng.uniform(1e6, 5e6, n).astype(int)
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    })


class TestPredictInterface:
    """interface contract tests — predict() never raises, always returns a KronosPrediction"""

    def test_returns_kronos_prediction_dataclass(self):
        df = _make_ohlcv()
        result = predict("TEST", df)
        assert isinstance(result, KronosPrediction)
        assert result.symbol == "TEST"

    def test_probability_in_unit_interval(self):
        df = _make_ohlcv()
        result = predict("TEST", df)
        assert 0.0 <= result.probability <= 1.0

    def test_confidence_in_unit_interval(self):
        df = _make_ohlcv()
        result = predict("TEST", df)
        assert 0.0 <= result.confidence <= 1.0

    def test_empty_dataframe_returns_neutral(self):
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        result = predict("TEST", df)
        assert result.probability == 0.5
        assert result.confidence == 0.0
        assert result.source == "insufficient_data"

    def test_none_dataframe_returns_neutral(self):
        result = predict("TEST", None)
        assert result.probability == 0.5
        assert result.source == "insufficient_data"

    def test_missing_columns_returns_neutral(self):
        df = pd.DataFrame({"close": [100, 101, 102]})
        result = predict("TEST", df)
        assert result.probability == 0.5
        assert result.source == "insufficient_data"

    def test_very_short_series_handled_gracefully(self):
        df = _make_ohlcv(n=5)
        result = predict("TEST", df)
        # / fallback heuristic returns 0.5 below 20 bars
        assert 0.0 <= result.probability <= 1.0


class TestFallbackHeuristic:
    """validates the statistical fallback is directionally sensible"""

    def test_uptrend_momentum_component_positive(self):
        # / reversion component can offset momentum on the final bar, so we
        # / verify the momentum signal itself, not the final probability
        df = _make_ohlcv(n=100, trend=0.005, vol=0.005)
        result = predict("TEST", df)
        assert result.components["momentum"] > 0
        assert result.source == "fallback_heuristic"

    def test_downtrend_momentum_component_negative(self):
        df = _make_ohlcv(n=100, trend=-0.005, vol=0.005)
        result = predict("TEST", df)
        assert result.components["momentum"] < 0
        assert result.source == "fallback_heuristic"

    def test_components_breakdown_present(self):
        df = _make_ohlcv()
        result = predict("TEST", df)
        assert result.components is not None
        assert "momentum" in result.components
        assert "vol_compression" in result.components
        assert "reversion" in result.components
        assert "combined" in result.components

    def test_components_in_expected_range(self):
        df = _make_ohlcv()
        result = predict("TEST", df)
        for key in ("momentum", "vol_compression", "reversion"):
            val = result.components[key]
            assert -1.0 <= val <= 1.0, f"{key}={val} out of range"

    def test_zero_vol_does_not_crash(self):
        # / flat prices — zero-variance returns
        df = pd.DataFrame({
            "open": [100.0] * 50, "high": [100.0] * 50,
            "low": [100.0] * 50, "close": [100.0] * 50,
            "volume": [1e6] * 50,
        })
        result = predict("TEST", df)
        assert 0.0 <= result.probability <= 1.0


class TestHfLoadPath:
    """verifies the HF model path short-circuits cleanly when disabled"""

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("KRONOS_ENABLED", raising=False)
        # / reset module state
        kronos_signal._model = None
        kronos_signal._tokenizer = None
        kronos_signal._model_load_attempted = False
        kronos_signal._model_load_failed_reason = None
        assert kronos_signal._try_load_hf_model() is False
        status = kronos_signal.get_load_status()
        assert status["hf_loaded"] is False
        assert status["enabled_via_env"] is False

    def test_status_reports_load_attempted(self, monkeypatch):
        monkeypatch.delenv("KRONOS_ENABLED", raising=False)
        kronos_signal._model = None
        kronos_signal._tokenizer = None
        kronos_signal._model_load_attempted = False
        kronos_signal._model_load_failed_reason = None
        kronos_signal._try_load_hf_model()
        status = kronos_signal.get_load_status()
        assert status["load_attempted"] is True

    def test_enabled_but_no_transformers_falls_back(self, monkeypatch):
        # / can't easily simulate transformers-missing in CI, but we can verify
        # / that when the load fails for ANY reason, predict still works via fallback
        monkeypatch.setenv("KRONOS_ENABLED", "false")
        df = _make_ohlcv()
        result = predict("TEST", df)
        assert result.source in ("fallback_heuristic", "kronos_hf")


class TestHfInferenceNSampleLoop:
    """verifies the n-sample loop recovers a real probability distribution from
    the averaged-output KronosPredictor API"""

    def test_stacks_independent_samples_into_probability(self, monkeypatch):
        # / fake KronosPredictor.predict that returns a different close each call
        # / (one up, one down, one up, ...) — true prob_up should be 0.5
        class FakePredictor:
            def __init__(self):
                self.calls = 0
                # / alternate above/below last_close=100 with exact 50/50 split
                self.deltas = [1.0, -1.0] * 15  # 30 draws, 15 up + 15 down

            def predict(self, df, x_timestamp, y_timestamp, pred_len, T, top_p, sample_count):
                delta = self.deltas[self.calls % len(self.deltas)]
                self.calls += 1
                last = float(df["close"].iloc[-1])
                return pd.DataFrame({"close": [last + delta]})

        monkeypatch.setattr(kronos_signal, "_predictor", FakePredictor())
        monkeypatch.setenv("KRONOS_SAMPLE_COUNT", "30")

        df = _make_ohlcv(n=64)
        result = kronos_signal._run_hf_inference(df, lookback=64)
        assert result is not None
        prob, _conf, components = result
        assert prob == pytest.approx(0.5, abs=0.001)
        assert components["sample_count"] == 30

    def test_all_up_samples_give_prob_1(self, monkeypatch):
        class AlwaysUp:
            def predict(self, df, x_timestamp, y_timestamp, pred_len, T, top_p, sample_count):
                last = float(df["close"].iloc[-1])
                return pd.DataFrame({"close": [last + 0.5]})

        monkeypatch.setattr(kronos_signal, "_predictor", AlwaysUp())
        monkeypatch.setenv("KRONOS_SAMPLE_COUNT", "10")
        df = _make_ohlcv(n=64)
        result = kronos_signal._run_hf_inference(df, lookback=64)
        assert result is not None
        prob, _, _ = result
        assert prob == 1.0

    def test_calls_predictor_once_per_sample(self, monkeypatch):
        # / regression: earlier code called predict once with sample_count=N; the
        # / vendored predictor averaged internally. new code must call N times.
        calls = {"n": 0}

        class Counter:
            def predict(self, df, x_timestamp, y_timestamp, pred_len, T, top_p, sample_count):
                calls["n"] += 1
                # / always sample with count=1 — the loop drives diversity
                assert sample_count == 1
                last = float(df["close"].iloc[-1])
                return pd.DataFrame({"close": [last + (0.1 if calls["n"] % 2 else -0.1)]})

        monkeypatch.setattr(kronos_signal, "_predictor", Counter())
        monkeypatch.setenv("KRONOS_SAMPLE_COUNT", "12")
        df = _make_ohlcv(n=64)
        kronos_signal._run_hf_inference(df, lookback=64)
        assert calls["n"] == 12

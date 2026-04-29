# / tests for dashboard indicator registry dispatch + response shapes

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.dashboard.indicator_registry import (
    REGISTRY,
    IndicatorSpec,
    available_indicators,
    compute,
)

# / canonical list of registered indicator ids (per phase1 spec)
EXPECTED_IDS = {
    "adx_14",
    "atr_14",
    "bb_20_2",
    "cci_20",
    "donchian_20",
    "ema_20",
    "ema_50",
    "ema_200",
    "fib_auto_100",
    "ichimoku_9_26_52_26",
    "keltner_20_10_2",
    "macd_12_26_9",
    "mfi_14",
    "obv",
    "psar_2_20",
    "roc_12",
    "rsi_14",
    "sma_20",
    "sma_50",
    "sma_200",
    "stoch_14_3_3",
    "supertrend_10_3",
    "vwap",
    "williams_14",
}


def _make_df(n: int = 300, seed: int = 42) -> pd.DataFrame:
    # / synthetic ohlcv frame with trending drift
    rng = np.random.default_rng(seed)
    close = np.cumsum(rng.normal(0.05, 1.0, n)) + 100.0
    high = close + rng.uniform(0.5, 2.0, n)
    low = close - rng.uniform(0.5, 2.0, n)
    open_ = close + rng.normal(0, 0.5, n)
    volume = rng.integers(10_000, 1_000_000, n).astype(float)
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


@pytest.fixture
def df():
    return _make_df()


class TestRegistryCatalog:
    def test_available_indicators_sorted(self):
        ids = available_indicators()
        assert ids == sorted(ids)

    def test_available_indicators_count(self):
        assert len(available_indicators()) == 24

    def test_all_expected_ids_present(self):
        assert set(available_indicators()) == EXPECTED_IDS

    def test_registry_values_are_indicator_specs(self):
        # / each registry entry is an IndicatorSpec dataclass
        for spec in REGISTRY.values():
            assert isinstance(spec, IndicatorSpec)

    def test_unknown_id_returns_none(self, df):
        assert compute(df, "not_a_real_indicator") is None

    def test_empty_id_returns_none(self, df):
        assert compute(df, "") is None


class TestComputeShapes:
    @pytest.mark.parametrize("ind_id", sorted(EXPECTED_IDS))
    def test_compute_returns_dict_with_pane(self, df, ind_id):
        result = compute(df, ind_id)
        assert result is not None, f"{ind_id} returned None"
        assert isinstance(result, dict)
        assert "pane" in result
        assert isinstance(result["pane"], str)

    @pytest.mark.parametrize("ind_id", sorted(EXPECTED_IDS - {"fib_auto_100"}))
    def test_per_bar_arrays_match_df_length(self, df, ind_id):
        # / every list-valued field is length-aligned to df
        result = compute(df, ind_id)
        assert result is not None
        for key, val in result.items():
            if isinstance(val, list):
                assert len(val) == len(df), (
                    f"{ind_id}.{key} length {len(val)} != {len(df)}"
                )

    def test_fib_auto_shape(self, df):
        # / fib_auto is static horizontal levels — not per-bar
        result = compute(df, "fib_auto_100")
        assert result is not None
        assert result["pane"] == "price"
        assert "levels" in result
        assert "swing_high" in result
        assert "swing_low" in result
        assert isinstance(result["levels"], dict)
        # / levels dict has exactly the 5 fib keys
        assert set(result["levels"].keys()) == {
            "level_236",
            "level_382",
            "level_500",
            "level_618",
            "level_786",
        }
        for v in result["levels"].values():
            assert isinstance(v, float)

    def test_bb_20_2_has_band_keys(self, df):
        result = compute(df, "bb_20_2")
        assert result is not None
        assert result["pane"] == "price"
        assert set(["upper", "middle", "lower"]).issubset(result.keys())

    def test_keltner_has_band_keys(self, df):
        result = compute(df, "keltner_20_10_2")
        assert result is not None
        assert set(["upper", "middle", "lower"]).issubset(result.keys())

    def test_donchian_has_band_keys(self, df):
        result = compute(df, "donchian_20")
        assert result is not None
        assert set(["upper", "middle", "lower"]).issubset(result.keys())

    def test_macd_has_line_signal_hist(self, df):
        result = compute(df, "macd_12_26_9")
        assert result is not None
        assert result["pane"] == "macd"
        assert set(["line", "signal", "hist"]).issubset(result.keys())

    def test_stoch_has_k_and_d(self, df):
        result = compute(df, "stoch_14_3_3")
        assert result is not None
        assert result["pane"] == "stoch"
        assert "k" in result and "d" in result

    def test_ichimoku_has_five_lines(self, df):
        result = compute(df, "ichimoku_9_26_52_26")
        assert result is not None
        assert result["pane"] == "price"
        for key in ("conversion", "base", "span_a", "span_b", "lagging"):
            assert key in result

    def test_psar_has_sar_and_direction(self, df):
        result = compute(df, "psar_2_20")
        assert result is not None
        assert result["pane"] == "price"
        assert "sar" in result and "direction" in result
        # / direction should be -1.0/1.0 floats (or None for length<2 warmup)
        for v in result["direction"]:
            if v is not None:
                assert v in (-1.0, 1.0)

    def test_supertrend_has_line_and_direction(self, df):
        result = compute(df, "supertrend_10_3")
        assert result is not None
        assert result["pane"] == "price"
        assert "line" in result and "direction" in result

    def test_single_value_shape_for_sma(self, df):
        # / sma uses the single-value "values" schema
        result = compute(df, "sma_20")
        assert result is not None
        assert result["pane"] == "price"
        assert "values" in result
        assert len(result["values"]) == len(df)


class TestComputeWarmupNulls:
    def test_sma_20_first_19_are_none(self, df):
        result = compute(df, "sma_20")
        vals = result["values"]
        for i in range(19):
            assert vals[i] is None, f"sma_20[{i}] should be None"
        # / index 19 onward should be floats
        assert isinstance(vals[19], float)
        assert isinstance(vals[20], float)

    def test_rsi_14_first_13_are_none(self, df):
        result = compute(df, "rsi_14")
        vals = result["values"]
        # / wilder rsi: ewm min_periods=14 on the diff-based gain/loss
        # / diff starts at index 1, so rsi valid from index 14 onward
        for i in range(13):
            assert vals[i] is None, f"rsi_14[{i}] should be None"

    def test_macd_12_26_9_warmup(self, df):
        # / macd_line = ema(12) - ema(26). ema(26) needs 26 bars -> first 25 line values None
        # / signal_line has ewm(9) on macd_line with min_periods=9 -> needs 25 + 8 = 33 None
        result = compute(df, "macd_12_26_9")
        line = result["line"]
        signal = result["signal"]
        for i in range(25):
            assert line[i] is None, f"macd line[{i}] should be None"
        # / line has a value at index 25
        assert isinstance(line[25], float)
        for i in range(33):
            assert signal[i] is None, f"macd signal[{i}] should be None"
        assert isinstance(signal[33], float)

    def test_ichimoku_warmup(self, df):
        result = compute(df, "ichimoku_9_26_52_26")
        conv = result["conversion"]
        base = result["base"]
        # / conversion: first 8 NaN (rolling 9)
        for i in range(8):
            assert conv[i] is None
        assert isinstance(conv[8], float)
        # / base: first 25 NaN (rolling 26)
        for i in range(25):
            assert base[i] is None
        assert isinstance(base[25], float)

    def test_sma_200_on_short_series(self):
        # / 60-bar series cannot compute sma_200 — should return all None, not raise
        short_df = _make_df(n=60)
        result = compute(short_df, "sma_200")
        assert result is not None
        vals = result["values"]
        assert len(vals) == 60
        assert all(v is None for v in vals)


class TestComputeNanHandling:
    def test_nan_becomes_none_in_output(self):
        # / inject NaN into close — sma_20 output should propagate None after warmup
        rng = np.random.default_rng(42)
        n = 60
        close = rng.normal(100, 5, n)
        # / inject NaN at index 40 (well past warmup)
        close[40] = np.nan
        df = pd.DataFrame({
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.full(n, 1_000_000.0),
        })
        result = compute(df, "sma_20")
        vals = result["values"]
        # / None values exist in the output (rolling window containing NaN -> NaN)
        assert any(v is None for v in vals[20:])

    def test_exception_in_compute_returns_none(self):
        # / missing 'volume' column should raise inside vwap and get caught
        rng = np.random.default_rng(42)
        n = 50
        close = rng.normal(100, 5, n)
        bad_df = pd.DataFrame({
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            # / no volume column
        })
        result = compute(bad_df, "vwap")
        assert result is None

    def test_missing_high_column_returns_none(self):
        # / atr_14 needs high/low/close — missing high -> caught exception
        rng = np.random.default_rng(42)
        n = 40
        close = rng.normal(100, 5, n)
        bad_df = pd.DataFrame({
            "open": close,
            "low": close - 1,
            "close": close,
            "volume": np.full(n, 1_000_000.0),
        })
        result = compute(bad_df, "atr_14")
        assert result is None


class TestComputeSpecificValues:
    def test_sma_20_at_index_19_equals_mean_1_to_20(self):
        # / close = [1..30], sma_20 at index 19 = mean(1..20) = 10.5
        n = 30
        close = np.arange(1, n + 1, dtype=float)
        df = pd.DataFrame({
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": np.full(n, 1000.0),
        })
        result = compute(df, "sma_20")
        vals = result["values"]
        assert vals[18] is None  # / need 20 bars, index 19 is first valid
        assert vals[19] == pytest.approx(10.5, abs=1e-10)
        # / sma_20 at index 20 = mean(2..21) = 11.5
        assert vals[20] == pytest.approx(11.5, abs=1e-10)

    def test_rsi_14_near_100_with_mostly_up_moves(self):
        # / almost all up moves with one tiny dip so avg_loss > 0 -> rsi near 100
        # / (pure uptrend makes avg_loss=0 and rsi=nan via replace(0,nan) in indicator)
        n = 40
        close = np.linspace(100, 200, n).copy()
        # / tiny dip at index 2 to seed non-zero loss
        close[2] = close[1] - 0.01
        df = pd.DataFrame({
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.full(n, 1000.0),
        })
        result = compute(df, "rsi_14")
        vals = result["values"]
        last = vals[-1]
        assert last is not None
        # / rsi should be very high (> 95) given near-monotonic uptrend
        assert last > 95.0

    def test_obv_monotonic_up_for_monotonic_close(self):
        # / monotonically increasing close => obv is cumulative volume (always up)
        n = 20
        close = np.arange(1, n + 1, dtype=float)
        volume = np.full(n, 100.0)
        df = pd.DataFrame({
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": volume,
        })
        result = compute(df, "obv")
        vals = result["values"]
        # / first bar has no previous -> direction is 0, so obv[0] = 0
        assert vals[0] == 0.0
        # / obv must be monotonically non-decreasing for monotonic uptrend
        for i in range(1, n):
            assert vals[i] >= vals[i - 1]
        # / final value equals (n-1) * 100 (n-1 up moves of 100 volume each)
        assert vals[-1] == pytest.approx((n - 1) * 100.0, abs=1e-10)

    def test_bb_middle_equals_sma(self, df):
        # / bollinger middle band should equal sma_20 values (same window)
        bb = compute(df, "bb_20_2")
        sm = compute(df, "sma_20")
        middle = bb["middle"]
        sma_vals = sm["values"]
        # / compare at a known valid index past warmup
        for i in range(25, 250, 25):
            if middle[i] is not None and sma_vals[i] is not None:
                assert middle[i] == pytest.approx(sma_vals[i], abs=1e-10)

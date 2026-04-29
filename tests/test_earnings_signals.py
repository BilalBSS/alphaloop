# / tests for earnings signals analysis

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.analysis.earnings_signals import (
    analyze_earnings,
    compute_earnings_signal,
    fetch_earnings,
)


class TestComputeEarningsSignal:
    def test_strong_beats(self):
        data = {
            "symbol": "AAPL",
            "quarters": [
                {"period": "Q1", "actual": 1.50, "estimate": 1.20, "surprise_pct": 0.25},
                {"period": "Q2", "actual": 1.40, "estimate": 1.10, "surprise_pct": 0.273},
                {"period": "Q3", "actual": 1.30, "estimate": 1.05, "surprise_pct": 0.238},
                {"period": "Q4", "actual": 1.20, "estimate": 1.00, "surprise_pct": 0.20},
            ],
        }
        result = compute_earnings_signal(data)
        assert result.signal == "bullish"
        assert result.strength > 50
        assert result.consecutive_beats >= 4
        assert result.surprise_pct == 0.25

    def test_strong_misses(self):
        data = {
            "symbol": "BAD",
            "quarters": [
                {"period": "Q1", "actual": 0.80, "estimate": 1.20, "surprise_pct": -0.333},
                {"period": "Q2", "actual": 0.90, "estimate": 1.10, "surprise_pct": -0.182},
                {"period": "Q3", "actual": 0.85, "estimate": 1.05, "surprise_pct": -0.190},
            ],
        }
        result = compute_earnings_signal(data)
        assert result.signal == "bearish"
        assert result.strength > 50
        assert result.consecutive_beats <= -3

    def test_mixed_results(self):
        data = {
            "symbol": "MIX",
            "quarters": [
                {"period": "Q1", "actual": 1.02, "estimate": 1.00, "surprise_pct": 0.02},
                {"period": "Q2", "actual": 0.98, "estimate": 1.00, "surprise_pct": -0.02},
            ],
        }
        result = compute_earnings_signal(data)
        # / small surprises within threshold -> neutral
        assert result.signal == "neutral"
        assert result.strength < 20

    def test_no_data(self):
        data = {"symbol": "EMPTY", "quarters": []}
        result = compute_earnings_signal(data)
        assert result.signal == "neutral"
        assert result.strength == 0.0
        assert result.surprise_pct is None

    def test_no_surprise_data(self):
        data = {
            "symbol": "PARTIAL",
            "quarters": [
                {"period": "Q1", "actual": 1.0, "estimate": None, "surprise_pct": None},
            ],
        }
        result = compute_earnings_signal(data)
        assert result.signal == "neutral"

    def test_single_big_beat(self):
        data = {
            "symbol": "BEAT",
            "quarters": [
                {"period": "Q1", "actual": 2.00, "estimate": 1.00, "surprise_pct": 1.0},
            ],
        }
        result = compute_earnings_signal(data)
        assert result.signal == "bullish"
        assert result.surprise_pct == 1.0

    def test_avg_surprise_calculated(self):
        data = {
            "symbol": "AVG",
            "quarters": [
                {"period": "Q1", "actual": 1.10, "estimate": 1.00, "surprise_pct": 0.10},
                {"period": "Q2", "actual": 1.20, "estimate": 1.00, "surprise_pct": 0.20},
            ],
        }
        result = compute_earnings_signal(data)
        assert result.avg_surprise_4q == 0.15


class TestComputeEarningsSignalDeep:
    def test_streak_breaks_on_threshold_boundary(self):
        # / exactly 2% surprise equals SURPRISE_THRESHOLD (0.02), not > so no beat
        data = {
            "symbol": "EDGE",
            "quarters": [
                {"period": "Q1", "actual": 1.02, "estimate": 1.00, "surprise_pct": 0.02},
            ],
        }
        result = compute_earnings_signal(data)
        # / 0.02 is not > 0.02, so no streak
        assert result.consecutive_beats == 0
        assert result.signal == "neutral"

    def test_streak_of_2_beats_then_miss(self):
        # / 2 beats followed by a miss — streak = 2
        data = {
            "symbol": "STREAK",
            "quarters": [
                {"period": "Q1", "actual": 1.20, "estimate": 1.00, "surprise_pct": 0.20},
                {"period": "Q2", "actual": 1.15, "estimate": 1.00, "surprise_pct": 0.15},
                {"period": "Q3", "actual": 0.80, "estimate": 1.00, "surprise_pct": -0.20},
            ],
        }
        result = compute_earnings_signal(data)
        assert result.consecutive_beats == 2

    def test_very_large_surprise_clamped(self):
        # / 100% surprise should be clamped to 5.0 by fetch, but compute handles it
        data = {
            "symbol": "HUGE",
            "quarters": [
                {"period": "Q1", "actual": 2.00, "estimate": 1.00, "surprise_pct": 5.0},
            ],
        }
        result = compute_earnings_signal(data)
        assert result.surprise_pct == 5.0
        # / surprise_points = min(40, 5.0/0.20*40) = min(40, 1000) = 40
        assert result.signal == "bullish"

    def test_negative_consecutive_beats(self):
        # / consecutive misses should be negative
        data = {
            "symbol": "MISS",
            "quarters": [
                {"period": "Q1", "actual": 0.80, "estimate": 1.00, "surprise_pct": -0.20},
                {"period": "Q2", "actual": 0.75, "estimate": 1.00, "surprise_pct": -0.25},
            ],
        }
        result = compute_earnings_signal(data)
        assert result.consecutive_beats == -2
        assert result.consecutive_beats < 0

    def test_strength_components_max(self):
        # / max possible: surprise 40 + streak 30 + avg 30 = 100
        data = {
            "symbol": "MAX",
            "quarters": [
                {"period": "Q1", "actual": 1.50, "estimate": 1.00, "surprise_pct": 0.50},
                {"period": "Q2", "actual": 1.40, "estimate": 1.00, "surprise_pct": 0.40},
                {"period": "Q3", "actual": 1.30, "estimate": 1.00, "surprise_pct": 0.30},
                {"period": "Q4", "actual": 1.20, "estimate": 1.00, "surprise_pct": 0.20},
            ],
        }
        result = compute_earnings_signal(data)
        # / surprise_points: min(40, 0.50/0.20*40)=40
        # / consecutive=4 >= 3 -> +30
        # / avg=0.35 > 0.05 -> min(30, 0.35/0.15*30)=min(30, 70)=30
        # / total=100
        assert result.strength == 100.0


class TestFetchEarningsDeep:
    @pytest.mark.asyncio
    async def test_handles_empty_quarterly_earnings(self):
        import pandas as pd

        mock_earnings = pd.DataFrame(columns=["Actual", "Estimate"])
        mock_ticker = MagicMock()
        mock_ticker.quarterly_earnings = mock_earnings
        mock_ticker.earnings_dates = None
        mock_yf = MagicMock()
        mock_yf.Ticker.return_value = mock_ticker

        with patch.dict("sys.modules", {"yfinance": mock_yf}):
            result = await fetch_earnings("EMPTY")
            assert result is not None
            assert result["quarters"] == []

    @pytest.mark.asyncio
    async def test_sorts_quarters_most_recent_first(self):
        import pandas as pd

        mock_earnings = pd.DataFrame(
            {"Actual": [1.0, 1.5, 1.2], "Estimate": [0.9, 1.3, 1.1]},
            index=["Q1 2025", "Q3 2025", "Q2 2025"],
        )
        mock_ticker = MagicMock()
        mock_ticker.quarterly_earnings = mock_earnings
        mock_ticker.earnings_dates = None
        mock_yf = MagicMock()
        mock_yf.Ticker.return_value = mock_ticker

        with patch.dict("sys.modules", {"yfinance": mock_yf}):
            result = await fetch_earnings("SORT")
            assert result is not None
            periods = [q["period"] for q in result["quarters"]]
            # / should be sorted descending by period string
            assert periods == sorted(periods, reverse=True)


class TestFetchEarnings:
    @pytest.mark.asyncio
    async def test_returns_data(self):
        import pandas as pd

        mock_earnings = pd.DataFrame(
            {"Actual": [1.5, 1.3], "Estimate": [1.2, 1.1]},
            index=["Q1 2026", "Q4 2025"],
        )
        mock_ticker = MagicMock()
        mock_ticker.quarterly_earnings = mock_earnings
        mock_ticker.earnings_dates = None
        mock_yf = MagicMock()
        mock_yf.Ticker.return_value = mock_ticker

        with patch.dict("sys.modules", {"yfinance": mock_yf}):
            result = await fetch_earnings("AAPL")
            assert result is not None
            assert result["symbol"] == "AAPL"
            assert len(result["quarters"]) == 2

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = Exception("network error")

        with patch.dict("sys.modules", {"yfinance": mock_yf}):
            result = await fetch_earnings("FAIL")
            assert result is None


class TestAnalyzeEarnings:
    @pytest.mark.asyncio
    async def test_full_pipeline(self):
        earnings_data = {
            "symbol": "AAPL",
            "quarters": [
                {"period": "Q1", "actual": 1.5, "estimate": 1.2, "surprise_pct": 0.25},
            ],
        }

        with patch("src.analysis.earnings_signals.fetch_earnings", return_value=earnings_data):
            result = await analyze_earnings("AAPL")
            assert result is not None
            assert result.symbol == "AAPL"
            assert result.signal == "bullish"

    @pytest.mark.asyncio
    async def test_returns_none_on_fetch_failure(self):
        with patch("src.analysis.earnings_signals.fetch_earnings", return_value=None):
            result = await analyze_earnings("FAIL")
            assert result is None

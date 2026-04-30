# / tests for fred macro data module

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.data.fred_macro import (
    _fetch_series,
    _normalize,
    fetch_macro_indicators,
    get_macro_score,
)


def _mock_pool(mock_conn):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


class TestNormalize:
    def test_neutral_value_returns_zero(self):
        assert _normalize("DGS10", 3.5) == 0.0

    def test_high_yield_positive(self):
        score = _normalize("DGS10", 6.0)
        assert score == 1.0

    def test_low_yield_negative(self):
        score = _normalize("DGS10", 1.0)
        assert score == -1.0

    def test_high_unemployment_bearish(self):
        # / high unemployment should be negative (bearish)
        score = _normalize("UNRATE", 7.5)
        assert score < 0

    def test_low_unemployment_bullish(self):
        score = _normalize("UNRATE", 3.0)
        assert score > 0

    def test_unknown_series(self):
        assert _normalize("UNKNOWN", 5.0) == 0.0

    def test_clamped_to_range(self):
        score = _normalize("DGS10", 100.0)
        assert score == 1.0
        score = _normalize("DGS10", -100.0)
        assert score == -1.0


class TestFetchSeries:
    @pytest.mark.asyncio
    async def test_returns_empty_without_key(self):
        # / phase 6 change: _fetch_series now returns list[dict] (all observations
        # / in window) instead of Optional[dict]. missing key returns empty list.
        with patch.dict(os.environ, {}, clear=True):
            result = await _fetch_series("DGS10")
            assert result == []

    @pytest.mark.asyncio
    async def test_parses_observation(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "observations": [{"date": "2024-01-15", "value": "4.25"}]
        }
        with patch.dict(os.environ, {"FRED_API_KEY": "test_key"}):
            with patch("src.data.fred_macro.api_get", new_callable=AsyncMock, return_value=mock_resp):
                result = await _fetch_series("DGS10")
                assert isinstance(result, list) and len(result) == 1
                assert result[0]["value"] == 4.25
                assert result[0]["series_id"] == "DGS10"

    @pytest.mark.asyncio
    async def test_skips_dot_values(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "observations": [
                {"date": "2024-01-15", "value": "."},
                {"date": "2024-01-14", "value": "3.5"},
            ]
        }
        with patch.dict(os.environ, {"FRED_API_KEY": "test_key"}):
            with patch("src.data.fred_macro.api_get", new_callable=AsyncMock, return_value=mock_resp):
                result = await _fetch_series("DGS10")
                # / only the non-dot observation should survive
                assert isinstance(result, list) and len(result) == 1
                assert result[0]["date"] == "2024-01-14"

    @pytest.mark.asyncio
    async def test_empty_observations(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"observations": []}
        with patch.dict(os.environ, {"FRED_API_KEY": "test_key"}):
            with patch("src.data.fred_macro.api_get", new_callable=AsyncMock, return_value=mock_resp):
                result = await _fetch_series("DGS10")
                assert result == []


class TestGetMacroScore:
    def test_mixed_indicators(self):
        indicators = {
            "DGS10": {"value": 4.0, "normalized": 0.2},
            "UNRATE": {"value": 4.0, "normalized": 0.167},
        }
        score = get_macro_score(indicators)
        assert -1.0 <= score <= 1.0

    def test_empty_indicators(self):
        assert get_macro_score({}) == 0.0

    def test_single_indicator(self):
        indicators = {"DGS10": {"value": 3.5, "normalized": 0.0}}
        assert get_macro_score(indicators) == 0.0

    def test_yield_curve_included(self):
        indicators = {
            "yield_curve_spread": {"value": -0.5, "normalized": -0.25},
        }
        score = get_macro_score(indicators)
        assert score == -0.25


class TestFetchMacroIndicators:
    @pytest.mark.asyncio
    async def test_stores_to_db(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "observations": [{"date": "2024-01-15", "value": "4.0"}]
        }
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)

        with patch.dict(os.environ, {"FRED_API_KEY": "test_key"}):
            with patch("src.data.fred_macro.api_get", new_callable=AsyncMock, return_value=mock_resp):
                result = await fetch_macro_indicators(pool)
                assert len(result) > 0
                # / phase 6: writes full window via executemany (not execute) so
                # / sparklines have historical points to draw
                mock_conn.executemany.assert_called()

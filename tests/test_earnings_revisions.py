# / tests for earnings revisions module

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.data.earnings_revisions import (
    compute_revision_momentum,
    fetch_earnings_estimates,
    store_earnings_estimates,
)


def _mock_pool(mock_conn):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


class TestFetchEarningsEstimates:
    @pytest.mark.asyncio
    async def test_returns_empty_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("src.data.earnings_revisions._fetch_estimates_yfinance", new_callable=AsyncMock, return_value=[]):
                result = await fetch_earnings_estimates("AAPL")
                assert result == []

    @pytest.mark.asyncio
    async def test_parses_estimates_finnhub(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"period": "2024-03-31", "epsAvg": 1.5, "epsHigh": 1.8, "epsLow": 1.2, "numberAnalysts": 30, "revenueAvg": 90000000000},
                {"period": "2024-06-30", "epsAvg": 1.6, "epsHigh": 1.9, "epsLow": 1.3, "numberAnalysts": 28, "revenueAvg": 92000000000},
            ]
        }
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "key"}):
            with patch("src.data.earnings_revisions._fetch_estimates_yfinance", new_callable=AsyncMock, return_value=[]):
                with patch("src.data.earnings_revisions.api_get", new_callable=AsyncMock, return_value=mock_resp):
                    result = await fetch_earnings_estimates("AAPL")
                    assert len(result) == 2
                    assert result[0]["eps_avg"] == 1.5

    @pytest.mark.asyncio
    async def test_parses_estimates_yfinance(self):
        yf_estimates = [
            {"symbol": "AAPL", "period": "0q", "eps_avg": 1.5, "eps_high": 1.8, "eps_low": 1.2, "source": "yfinance"},
        ]
        with patch("src.data.earnings_revisions._fetch_estimates_yfinance", new_callable=AsyncMock, return_value=yf_estimates):
            result = await fetch_earnings_estimates("AAPL")
            assert len(result) == 1
            assert result[0]["eps_avg"] == 1.5
            assert result[0]["source"] == "yfinance"


class TestRevisionMomentum:
    def test_positive_momentum(self):
        estimates = [
            {"eps_avg": 1.6},
            {"eps_avg": 1.4},
        ]
        momentum = compute_revision_momentum(estimates)
        assert momentum > 0

    def test_negative_momentum(self):
        estimates = [
            {"eps_avg": 1.2},
            {"eps_avg": 1.5},
        ]
        momentum = compute_revision_momentum(estimates)
        assert momentum < 0

    def test_no_change(self):
        estimates = [
            {"eps_avg": 1.5},
            {"eps_avg": 1.5},
        ]
        assert compute_revision_momentum(estimates) == 0.0

    def test_insufficient_data(self):
        assert compute_revision_momentum([{"eps_avg": 1.5}]) == 0.0
        assert compute_revision_momentum([]) == 0.0

    def test_none_values(self):
        estimates = [{"eps_avg": None}, {"eps_avg": 1.5}]
        assert compute_revision_momentum(estimates) == 0.0

    def test_near_zero_previous(self):
        estimates = [{"eps_avg": 0.5}, {"eps_avg": 0.0001}]
        assert compute_revision_momentum(estimates) == 0.0

    def test_clamped(self):
        estimates = [{"eps_avg": 100.0}, {"eps_avg": 0.01}]
        assert compute_revision_momentum(estimates) == 1.0


class TestStoreEarningsEstimates:
    @pytest.mark.asyncio
    async def test_stores_estimates(self):
        estimates = [{
            "symbol": "AAPL",
            "period": "2024-03-31",
            "eps_avg": 1.5,
            "revenue_avg": 90000000000,
        }]
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        count = await store_earnings_estimates(pool, estimates)
        assert count == 1
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_estimates(self):
        pool = MagicMock()
        count = await store_earnings_estimates(pool, [])
        assert count == 0

# / tests for corporate events module

import os
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.data.corporate_events import (
    days_to_earnings,
    fetch_dividends,
    fetch_earnings_calendar,
    store_corporate_event,
)


def _mock_pool(mock_conn):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


class TestFetchEarningsCalendar:
    @pytest.mark.asyncio
    async def test_returns_none_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            result = await fetch_earnings_calendar("AAPL")
            assert result is None

    @pytest.mark.asyncio
    async def test_parses_calendar(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "earningsCalendar": [
                {"date": "2025-01-30", "epsEstimate": 2.1, "revenueEstimate": 125000000000, "hour": "amc"},
            ]
        }
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "key"}):
            with patch("src.data.corporate_events.api_get", new_callable=AsyncMock, return_value=mock_resp):
                result = await fetch_earnings_calendar("AAPL")
                assert result is not None
                assert result["eps_estimate"] == 2.1

    @pytest.mark.asyncio
    async def test_empty_calendar(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"earningsCalendar": []}
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "key"}):
            with patch("src.data.corporate_events.api_get", new_callable=AsyncMock, return_value=mock_resp):
                result = await fetch_earnings_calendar("AAPL")
                assert result is None


class TestFetchDividends:
    @pytest.mark.asyncio
    async def test_returns_empty_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            result = await fetch_dividends("AAPL")
            assert result == []

    @pytest.mark.asyncio
    async def test_parses_dividends(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"payDate": "2024-02-15", "amount": 0.24, "currency": "USD"},
        ]
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "key"}):
            with patch("src.data.corporate_events.api_get", new_callable=AsyncMock, return_value=mock_resp):
                result = await fetch_dividends("AAPL")
                assert len(result) == 1
                assert result[0]["amount"] == 0.24


class TestDaysToEarnings:
    @pytest.mark.asyncio
    async def test_returns_days(self):
        future_date = "2099-12-31"
        mock_cal = {"date": future_date, "eps_estimate": 2.1}
        with patch("src.data.corporate_events.fetch_earnings_calendar", new_callable=AsyncMock, return_value=mock_cal):
            result = await days_to_earnings("AAPL")
            assert result is not None
            assert result > 0

    @pytest.mark.asyncio
    async def test_returns_none_no_data(self):
        with patch("src.data.corporate_events.fetch_earnings_calendar", new_callable=AsyncMock, return_value=None):
            with patch("src.data.corporate_events._fetch_yf_calendar_sync", return_value=None):
                result = await days_to_earnings("AAPL")
                assert result is None


class TestStoreCorporateEvent:
    @pytest.mark.asyncio
    async def test_stores_event(self):
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        await store_corporate_event(pool, "AAPL", "earnings", date(2024, 1, 30), {"hour": "amc"})
        mock_conn.execute.assert_called_once()
        args = mock_conn.execute.call_args[0]
        assert "corporate_events" in args[0]

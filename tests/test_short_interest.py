# / tests for short interest module

import os
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.data.short_interest import (
    fetch_finra_short_volume,
    fetch_short_interest,
    store_short_interest,
)


def _mock_pool(mock_conn):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


class TestFetchShortInterest:
    @pytest.mark.asyncio
    async def test_returns_none_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("src.data.short_interest._fetch_short_yfinance", new_callable=AsyncMock, return_value=None):
                with patch("src.data.short_interest.fetch_finra_short_volume", new_callable=AsyncMock, return_value=None):
                    result = await fetch_short_interest("AAPL")
                    assert result is None

    @pytest.mark.asyncio
    async def test_parses_data_finnhub(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"settlementDate": "2024-01-15", "shortInterest": 50000000}]
        }
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "key"}):
            with patch("src.data.short_interest._fetch_short_yfinance", new_callable=AsyncMock, return_value=None):
                with patch("src.data.short_interest.api_get", new_callable=AsyncMock, return_value=mock_resp):
                    result = await fetch_short_interest("AAPL")
                    assert result is not None
                    assert result["short_interest"] == 50000000

    @pytest.mark.asyncio
    async def test_parses_data_yfinance(self):
        yf_data = {
            "symbol": "AAPL",
            "date": "2024-01-15",
            "short_volume": 5000000,
            "total_volume": 15000000,
            "short_ratio": 2.5,
            "short_percent_float": 0.035,
        }
        with patch("src.data.short_interest._fetch_short_yfinance", new_callable=AsyncMock, return_value=yf_data):
            result = await fetch_short_interest("AAPL")
            assert result is not None
            assert result["short_percent_float"] == 0.035

    @pytest.mark.asyncio
    async def test_empty_data(self):
        with patch("src.data.short_interest._fetch_short_yfinance", new_callable=AsyncMock, return_value=None):
            with patch.dict(os.environ, {"FINNHUB_API_KEY": "key"}):
                mock_resp = MagicMock()
                mock_resp.json.return_value = {"data": []}
                with patch("src.data.short_interest.api_get", new_callable=AsyncMock, return_value=mock_resp):
                    with patch("src.data.short_interest.fetch_finra_short_volume", new_callable=AsyncMock, return_value=None):
                        result = await fetch_short_interest("AAPL")
                        assert result is None


class TestFetchFinraShortVolume:
    @pytest.mark.asyncio
    async def test_parses_pipe_delimited(self):
        mock_resp = MagicMock()
        mock_resp.text = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n20240115|AAPL|5000000|100000|15000000|N"
        with patch("src.data.short_interest.api_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_finra_short_volume("AAPL", date(2024, 1, 15))
            assert result is not None
            assert result["short_volume"] == 5000000
            assert result["total_volume"] == 15000000
            assert 0 < result["short_ratio"] < 1

    @pytest.mark.asyncio
    async def test_symbol_not_found(self):
        mock_resp = MagicMock()
        mock_resp.text = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n20240115|MSFT|1000|100|5000|N"
        with patch("src.data.short_interest.api_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_finra_short_volume("AAPL", date(2024, 1, 15))
            assert result is None

    @pytest.mark.asyncio
    async def test_handles_error(self):
        with patch("src.data.short_interest.api_get", new_callable=AsyncMock, side_effect=Exception("fail")):
            result = await fetch_finra_short_volume("AAPL", date(2024, 1, 15))
            assert result is None


class TestStoreShortInterest:
    @pytest.mark.asyncio
    async def test_stores_data(self):
        data = {
            "symbol": "AAPL",
            "date": "2024-01-15",
            "short_volume": 5000000,
            "total_volume": 15000000,
            "short_ratio": 0.3333,
        }
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        await store_short_interest(pool, data)
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_none_data(self):
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        await store_short_interest(pool, None)
        mock_conn.execute.assert_not_called()

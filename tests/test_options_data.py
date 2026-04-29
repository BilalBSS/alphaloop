# / tests for options data module

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from src.data.options_data import (
    _compute_max_pain,
    _fetch_options_sync,
    fetch_options_data,
    store_options_data,
)


def _mock_pool(mock_conn):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


class TestComputeMaxPain:
    def test_basic_max_pain(self):
        calls = pd.DataFrame({
            "strike": [100.0, 110.0, 120.0],
            "openInterest": [100, 200, 50],
        })
        puts = pd.DataFrame({
            "strike": [100.0, 110.0, 120.0],
            "openInterest": [50, 150, 100],
        })
        result = _compute_max_pain(calls, puts)
        assert result is not None
        assert isinstance(result, float)

    def test_empty_chains(self):
        calls = pd.DataFrame()
        puts = pd.DataFrame()
        assert _compute_max_pain(calls, puts) is None

    def test_calls_only(self):
        calls = pd.DataFrame({
            "strike": [100.0, 110.0],
            "openInterest": [100, 200],
        })
        puts = pd.DataFrame()
        result = _compute_max_pain(calls, puts)
        assert result is not None


class TestFetchOptionsData:
    @pytest.mark.asyncio
    async def test_skips_crypto(self):
        result = await fetch_options_data("BTC-USD")
        assert result is None

    @pytest.mark.asyncio
    async def test_fetches_equity(self):
        mock_result = {
            "symbol": "AAPL",
            "iv_current": 0.3,
            "iv_rank": 0.5,
            "put_call_ratio": 0.8,
            "max_pain": 175.0,
        }
        with patch("src.data.options_data._fetch_options_sync", return_value=mock_result):
            result = await fetch_options_data("AAPL")
            assert result is not None
            assert result["iv_rank"] == 0.5

    @pytest.mark.asyncio
    async def test_handles_error(self):
        with patch("src.data.options_data._fetch_options_sync", side_effect=Exception("fail")):
            result = await fetch_options_data("AAPL")
            assert result is None


class TestFetchOptionsSync:
    def test_returns_none_no_options(self):
        mock_ticker = MagicMock()
        mock_ticker.options = []
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = _fetch_options_sync("AAPL")
            assert result is None


class TestStoreOptionsData:
    @pytest.mark.asyncio
    async def test_stores_data(self):
        data = {
            "symbol": "AAPL",
            "iv_current": 0.3,
            "iv_rank": 0.5,
            "put_call_ratio": 0.8,
            "max_pain": 175.0,
        }
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        await store_options_data(pool, data)
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_none_data(self):
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        await store_options_data(pool, None)
        mock_conn.execute.assert_not_called()

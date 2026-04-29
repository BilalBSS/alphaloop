# / tests for congressional trades module

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.data.congressional_trades import (
    compute_net_buy_ratio,
    fetch_congressional_trades,
    store_congressional_trades,
)


def _mock_pool(mock_conn):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


class TestFetchCongressionalTrades:
    @pytest.mark.asyncio
    async def test_returns_empty_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("src.data.congressional_trades._fetch_senate_trades", new_callable=AsyncMock, return_value=[]):
                result = await fetch_congressional_trades("AAPL")
                assert result == []

    @pytest.mark.asyncio
    async def test_parses_trades_finnhub(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {
                    "transactionDate": "2024-01-15",
                    "name": "Sen. Test",
                    "transactionType": "Purchase",
                    "amountRange": "$1,001 - $15,000",
                },
            ]
        }
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "key"}):
            with patch("src.data.congressional_trades._fetch_senate_trades", new_callable=AsyncMock, return_value=[]):
                with patch("src.data.congressional_trades.api_get", new_callable=AsyncMock, return_value=mock_resp):
                    result = await fetch_congressional_trades("AAPL")
                    assert len(result) == 1
                    assert result[0]["name"] == "Sen. Test"
                    assert result[0]["transaction_type"] == "Purchase"

    @pytest.mark.asyncio
    async def test_parses_trades_senate(self):
        senate_data = [
            {
                "ticker": "AAPL",
                "transaction_date": "2024-01-15",
                "senator": "Sen. Test",
                "type": "Purchase",
                "amount": "$1,001 - $15,000",
            },
        ]
        with patch("src.data.congressional_trades._fetch_senate_bulk", new_callable=AsyncMock, return_value=senate_data):
            result = await fetch_congressional_trades("AAPL")
            assert len(result) == 1
            assert result[0]["name"] == "Sen. Test"
            assert result[0]["transaction_type"] == "Purchase"

    @pytest.mark.asyncio
    async def test_empty_data(self):
        with patch("src.data.congressional_trades._fetch_senate_trades", new_callable=AsyncMock, return_value=[]):
            with patch.dict(os.environ, {"FINNHUB_API_KEY": "key"}):
                mock_resp = MagicMock()
                mock_resp.json.return_value = {"data": []}
                with patch("src.data.congressional_trades.api_get", new_callable=AsyncMock, return_value=mock_resp):
                    result = await fetch_congressional_trades("AAPL")
                    assert result == []


class TestStoreCongressionalTrades:
    @pytest.mark.asyncio
    async def test_stores_trades(self):
        trades = [{
            "symbol": "AAPL",
            "filing_date": "2024-01-15",
            "name": "Sen. Test",
            "transaction_type": "Purchase",
            "amount_range": "$1,001 - $15,000",
        }]
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        count = await store_congressional_trades(pool, trades)
        assert count == 1
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_trades(self):
        pool = MagicMock()
        count = await store_congressional_trades(pool, [])
        assert count == 0

    @pytest.mark.asyncio
    async def test_skips_bad_date(self):
        trades = [{
            "symbol": "AAPL",
            "filing_date": "",
            "name": "Test",
            "transaction_type": "Purchase",
            "amount_range": "",
        }]
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        count = await store_congressional_trades(pool, trades)
        assert count == 0


class TestComputeNetBuyRatio:
    def test_all_purchases(self):
        trades = [
            {"transaction_type": "Purchase"},
            {"transaction_type": "Purchase"},
        ]
        assert compute_net_buy_ratio(trades) == 1.0

    def test_all_sales(self):
        trades = [
            {"transaction_type": "Sale"},
            {"transaction_type": "Sale (Full)"},
        ]
        assert compute_net_buy_ratio(trades) == -1.0

    def test_mixed(self):
        trades = [
            {"transaction_type": "Purchase"},
            {"transaction_type": "Sale"},
        ]
        assert compute_net_buy_ratio(trades) == 0.0

    def test_empty(self):
        assert compute_net_buy_ratio([]) == 0.0

    def test_no_recognized_types(self):
        trades = [{"transaction_type": "Exchange"}]
        assert compute_net_buy_ratio(trades) == 0.0

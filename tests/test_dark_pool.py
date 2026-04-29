# / tests for dark pool module

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.data.dark_pool import (
    fetch_dark_pool_data,
    store_dark_pool,
)


def _mock_pool(mock_conn):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


class TestFetchDarkPoolData:
    @pytest.mark.asyncio
    async def test_parses_response(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"weekStartDate": "2024-01-15", "totalWeeklyShareQuantity": 5000000, "totalWeeklyTradeCount": 1000}
        ]
        with patch("src.data.dark_pool.api_post", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_dark_pool_data("AAPL")
            assert result is not None
            assert result["ats_volume"] == 5000000
            assert result["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_empty_response(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        with patch("src.data.dark_pool.api_post", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_dark_pool_data("AAPL")
            assert result is None

    @pytest.mark.asyncio
    async def test_handles_error(self):
        with patch("src.data.dark_pool.api_post", new_callable=AsyncMock, side_effect=Exception("fail")):
            result = await fetch_dark_pool_data("AAPL")
            assert result is None

    @pytest.mark.asyncio
    async def test_computes_ratio_from_pool(self):
        # / when pool provided, sums market_data volume for the week and computes ratio
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"weekStartDate": "2024-01-15", "totalWeeklyShareQuantity": 2_000_000, "totalWeeklyTradeCount": 500}
        ]
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"total": 10_000_000}
        pool = _mock_pool(mock_conn)
        with patch("src.data.dark_pool.api_post", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_dark_pool_data("AAPL", pool=pool)
            assert result is not None
            assert result["total_volume"] == 10_000_000
            assert result["dark_pool_ratio"] == pytest.approx(0.20)

    @pytest.mark.asyncio
    async def test_ratio_none_when_no_total_volume(self):
        # / pool provided but market_data has no rows → ratio stays None (no div by zero)
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"weekStartDate": "2024-01-15", "totalWeeklyShareQuantity": 2_000_000, "totalWeeklyTradeCount": 500}
        ]
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"total": 0}
        pool = _mock_pool(mock_conn)
        with patch("src.data.dark_pool.api_post", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_dark_pool_data("AAPL", pool=pool)
            assert result is not None
            assert result["total_volume"] is None
            assert result["dark_pool_ratio"] is None

    @pytest.mark.asyncio
    async def test_ratio_clamped_to_one(self):
        # / if ats_volume somehow exceeds total (data anomaly), ratio clamped to 1.0
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"weekStartDate": "2024-01-15", "totalWeeklyShareQuantity": 15_000_000, "totalWeeklyTradeCount": 500}
        ]
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"total": 10_000_000}
        pool = _mock_pool(mock_conn)
        with patch("src.data.dark_pool.api_post", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_dark_pool_data("AAPL", pool=pool)
            assert result is not None
            assert result["dark_pool_ratio"] == pytest.approx(1.0)


class TestStoreDarkPool:
    @pytest.mark.asyncio
    async def test_stores_data(self):
        data = {
            "symbol": "AAPL",
            "week_start": "2024-01-15",
            "ats_volume": 5000000,
            "total_volume": None,
            "dark_pool_ratio": None,
        }
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        await store_dark_pool(pool, data)
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_none_data(self):
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        await store_dark_pool(pool, None)
        mock_conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_empty_date(self):
        data = {
            "symbol": "AAPL",
            "week_start": "",
            "ats_volume": 5000000,
            "total_volume": None,
            "dark_pool_ratio": None,
        }
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        await store_dark_pool(pool, data)
        mock_conn.execute.assert_not_called()

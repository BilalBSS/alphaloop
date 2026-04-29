# / tests for analyst ratings module

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.data.analyst_ratings import (
    compute_consensus_score,
    compute_target_upside,
    fetch_analyst_ratings,
    store_analyst_ratings,
)


def _mock_pool(mock_conn):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


class TestConsensusScore:
    def test_all_strong_buy(self):
        rec = {"strongBuy": 10, "buy": 0, "hold": 0, "sell": 0, "strongSell": 0}
        assert compute_consensus_score(rec) == 1.0

    def test_all_strong_sell(self):
        rec = {"strongBuy": 0, "buy": 0, "hold": 0, "sell": 0, "strongSell": 10}
        assert compute_consensus_score(rec) == -1.0

    def test_all_hold(self):
        rec = {"strongBuy": 0, "buy": 0, "hold": 10, "sell": 0, "strongSell": 0}
        assert compute_consensus_score(rec) == 0.0

    def test_mixed(self):
        rec = {"strongBuy": 5, "buy": 5, "hold": 5, "sell": 5, "strongSell": 5}
        assert compute_consensus_score(rec) == 0.0

    def test_empty(self):
        rec = {"strongBuy": 0, "buy": 0, "hold": 0, "sell": 0, "strongSell": 0}
        assert compute_consensus_score(rec) == 0.0

    def test_bullish_bias(self):
        rec = {"strongBuy": 10, "buy": 5, "hold": 3, "sell": 1, "strongSell": 0}
        score = compute_consensus_score(rec)
        assert score > 0.5


class TestTargetUpside:
    def test_positive_upside(self):
        result = compute_target_upside(200.0, 150.0)
        assert result is not None
        assert abs(result - 0.3333) < 0.01

    def test_negative_upside(self):
        result = compute_target_upside(100.0, 150.0)
        assert result is not None
        assert result < 0

    def test_none_target(self):
        assert compute_target_upside(None, 150.0) is None

    def test_none_price(self):
        assert compute_target_upside(200.0, None) is None

    def test_zero_price(self):
        assert compute_target_upside(200.0, 0) is None


class TestFetchAnalystRatings:
    @pytest.mark.asyncio
    async def test_fetches_ratings(self):
        mock_rec_resp = MagicMock()
        mock_rec_resp.json.return_value = [{
            "strongBuy": 10, "buy": 5, "hold": 3, "sell": 1, "strongSell": 0,
            "period": "2024-01-01",
        }]
        mock_target_resp = MagicMock()
        mock_target_resp.json.return_value = {
            "targetHigh": 200.0, "targetLow": 150.0, "targetMean": 180.0,
        }
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "key"}):
            with patch("src.data.analyst_ratings._fetch_analyst_yfinance", new_callable=AsyncMock, return_value={}):
                with patch("src.data.analyst_ratings._fetch_recommendations", new_callable=AsyncMock, return_value=mock_rec_resp.json.return_value):
                    with patch("src.data.analyst_ratings._fetch_price_target", new_callable=AsyncMock, return_value=mock_target_resp.json.return_value):
                        result = await fetch_analyst_ratings("AAPL")
                        assert result["strong_buy"] == 10
                        assert result["target_mean"] == 180.0
                        assert result["consensus_score"] > 0


class TestStoreAnalystRatings:
    @pytest.mark.asyncio
    async def test_stores_to_db(self):
        data = {
            "strong_buy": 10, "buy": 5, "hold": 3, "sell": 1, "strong_sell": 0,
            "target_high": 200.0, "target_low": 150.0, "target_mean": 180.0,
        }
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        await store_analyst_ratings(pool, "AAPL", data)
        mock_conn.execute.assert_called_once()
        args = mock_conn.execute.call_args[0]
        assert "analyst_ratings" in args[0]

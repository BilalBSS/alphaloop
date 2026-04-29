# / tests for news sentiment (finnhub + keyword scoring)

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.data.news_sentiment import (
    _keyword_score,
    compute_sentiment_score,
    fetch_company_news,
    store_sentiment,
)


class TestKeywordScore:
    def test_positive_text(self):
        score = _keyword_score("Company beats estimates with record revenue growth")
        assert score > 0

    def test_negative_text(self):
        score = _keyword_score("Stock crashes after company misses estimates, announces layoffs")
        assert score < 0

    def test_neutral_text(self):
        score = _keyword_score("Company announces quarterly results")
        assert score == 0.0

    def test_mixed_text(self):
        # / one positive, one negative = neutral
        score = _keyword_score("Revenue growth but profit decline")
        assert -0.5 <= score <= 0.5

    def test_empty_text(self):
        assert _keyword_score("") == 0.0

    def test_range_bounded(self):
        # / score should always be between -1 and 1
        score = _keyword_score("beat exceed record growth upgrade buy outperform bullish strong surge rally profit")
        assert -1.0 <= score <= 1.0


class TestFetchCompanyNews:
    @pytest.mark.asyncio
    async def test_returns_articles(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"headline": "Apple beats Q4 estimates", "source": "Reuters"},
        ]
        mock_resp.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"FINNHUB_API_KEY": "key"}):
            with patch("src.data.news_sentiment.api_get", new_callable=AsyncMock, return_value=mock_resp):
                result = await fetch_company_news("AAPL")
                assert len(result) == 1

    @pytest.mark.asyncio
    async def test_returns_empty_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            result = await fetch_company_news("AAPL")
            assert result == []


class TestComputeSentimentScore:
    @pytest.mark.asyncio
    async def test_scores_positive_headlines(self):
        with patch("src.data.news_sentiment.fetch_company_news", new_callable=AsyncMock) as mock_news:
            mock_news.return_value = [
                {"headline": "Company beats estimates with strong growth"},
                {"headline": "Record revenue and profit"},
            ]
            score = await compute_sentiment_score("AAPL")
            assert score > 0

    @pytest.mark.asyncio
    async def test_returns_zero_no_articles(self):
        with patch("src.data.news_sentiment.fetch_company_news", new_callable=AsyncMock) as mock_news:
            mock_news.return_value = []
            score = await compute_sentiment_score("AAPL")
            assert score == 0.0

    @pytest.mark.asyncio
    async def test_returns_zero_on_failure(self):
        import httpx
        with patch("src.data.news_sentiment.fetch_company_news",
                   side_effect=httpx.HTTPError("err")):
            score = await compute_sentiment_score("AAPL")
            assert score == 0.0


class TestStoreSentiment:
    @pytest.mark.asyncio
    async def test_stores_to_db(self):
        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        await store_sentiment(mock_pool, "AAPL", 0.75, "finnhub")
        mock_conn.execute.assert_called_once()
        args = mock_conn.execute.call_args[0]
        assert "news_sentiment" in args[0]
        assert args[1] == "AAPL"

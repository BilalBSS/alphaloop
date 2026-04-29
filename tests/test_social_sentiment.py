# / tests for social sentiment (apewisdom + fear & greed + vix)

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.data.social_sentiment import (
    _fetch_vix_sync,
    fetch_apewisdom,
    fetch_fear_greed_index,
    fetch_stocktwits_sentiment,
    fetch_vix,
    run_social_sentiment,
    store_social_sentiment,
)


def _mock_pool():
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_pool, mock_conn


def _stocktwits_response(bullish: int, bearish: int, msg_count: int = 5):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "symbol": {
            "sentiments": {"bullish": bullish, "bearish": bearish},
        },
        "messages": [{"id": i} for i in range(msg_count)],
    }
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def _fng_response(value: str):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": [{"value": value, "value_classification": "Neutral"}],
    }
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


class TestFetchStockTwitsSentiment:
    @pytest.mark.asyncio
    async def test_returns_bullish_data(self):
        resp = _stocktwits_response(80, 20, msg_count=10)
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=resp):
            result = await fetch_stocktwits_sentiment("AAPL")
            assert result is not None
            assert result["bullish_pct"] == pytest.approx(0.8)
            assert result["bearish_pct"] == pytest.approx(0.2)
            assert result["volume"] == 10
            assert result["raw_score"] == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_returns_bearish_data(self):
        resp = _stocktwits_response(10, 90)
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=resp):
            result = await fetch_stocktwits_sentiment("TSLA")
            assert result is not None
            assert result["bullish_pct"] == pytest.approx(0.1)
            assert result["bearish_pct"] == pytest.approx(0.9)
            assert result["raw_score"] == pytest.approx(-0.8)

    @pytest.mark.asyncio
    async def test_even_split(self):
        resp = _stocktwits_response(50, 50)
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=resp):
            result = await fetch_stocktwits_sentiment("MSFT")
            assert result is not None
            assert result["bullish_pct"] == pytest.approx(0.5)
            assert result["bearish_pct"] == pytest.approx(0.5)
            assert result["raw_score"] == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_returns_none_no_sentiments(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"symbol": {}, "messages": []}
        mock_resp.raise_for_status = MagicMock()

        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_stocktwits_sentiment("UNKNOWN")
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_zero_total(self):
        resp = _stocktwits_response(0, 0)
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=resp):
            result = await fetch_stocktwits_sentiment("XYZ")
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, side_effect=Exception("404")):
            result = await fetch_stocktwits_sentiment("BAD")
            assert result is None

    @pytest.mark.asyncio
    async def test_all_bullish(self):
        resp = _stocktwits_response(100, 0)
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=resp):
            result = await fetch_stocktwits_sentiment("MOON")
            assert result is not None
            assert result["bullish_pct"] == pytest.approx(1.0)
            assert result["bearish_pct"] == pytest.approx(0.0)
            assert result["raw_score"] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_all_bearish(self):
        resp = _stocktwits_response(0, 100)
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=resp):
            result = await fetch_stocktwits_sentiment("DUMP")
            assert result is not None
            assert result["raw_score"] == pytest.approx(-1.0)


class TestFetchFearGreedIndex:
    @pytest.mark.asyncio
    async def test_neutral_value(self):
        resp = _fng_response("50")
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=resp):
            result = await fetch_fear_greed_index()
            assert result is not None
            assert result["raw_value"] == pytest.approx(50.0)
            assert result["normalized"] == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_extreme_fear(self):
        resp = _fng_response("0")
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=resp):
            result = await fetch_fear_greed_index()
            assert result is not None
            assert result["normalized"] == pytest.approx(-1.0)

    @pytest.mark.asyncio
    async def test_extreme_greed(self):
        resp = _fng_response("100")
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=resp):
            result = await fetch_fear_greed_index()
            assert result is not None
            assert result["normalized"] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_fear_region(self):
        # / value=25 -> (25-50)/50 = -0.5
        resp = _fng_response("25")
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=resp):
            result = await fetch_fear_greed_index()
            assert result is not None
            assert result["normalized"] == pytest.approx(-0.5)

    @pytest.mark.asyncio
    async def test_greed_region(self):
        # / value=75 -> (75-50)/50 = 0.5
        resp = _fng_response("75")
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=resp):
            result = await fetch_fear_greed_index()
            assert result is not None
            assert result["normalized"] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_returns_none_no_data(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [{}]}
        mock_resp.raise_for_status = MagicMock()

        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_fear_greed_index()
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_empty_data(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": []}
        mock_resp.raise_for_status = MagicMock()

        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_fear_greed_index()
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, side_effect=Exception("timeout")):
            result = await fetch_fear_greed_index()
            assert result is None

    @pytest.mark.asyncio
    async def test_clamps_above_100(self):
        # / edge case: value > 100 should clamp to 1.0
        resp = _fng_response("110")
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=resp):
            result = await fetch_fear_greed_index()
            assert result is not None
            assert result["normalized"] <= 1.0


class TestStoreSocialSentiment:
    @pytest.mark.asyncio
    async def test_stores_to_db(self):
        pool, conn = _mock_pool()
        await store_social_sentiment(pool, "AAPL", "stocktwits", 0.8, 0.2, 50, 0.6)
        conn.execute.assert_called_once()
        args = conn.execute.call_args[0]
        assert "social_sentiment" in args[0]
        assert args[1] == "AAPL"
        assert args[3] == "stocktwits"

    @pytest.mark.asyncio
    async def test_stores_with_none_values(self):
        # / fear & greed stores with null bullish/bearish/volume
        pool, conn = _mock_pool()
        await store_social_sentiment(pool, "BTC-USD", "fear_greed", None, None, None, 0.3)
        conn.execute.assert_called_once()
        args = conn.execute.call_args[0]
        assert args[4] is None  # bullish_pct
        assert args[5] is None  # bearish_pct
        assert args[6] is None  # volume

    @pytest.mark.asyncio
    async def test_upsert_query(self):
        pool, conn = _mock_pool()
        await store_social_sentiment(pool, "ETH-USD", "stocktwits", 0.5, 0.5, 10, 0.0)
        sql = conn.execute.call_args[0][0]
        assert "ON CONFLICT" in sql
        assert "DO UPDATE" in sql


class TestRunSocialSentiment:
    @pytest.mark.asyncio
    async def test_processes_multiple_symbols(self):
        pool, _conn = _mock_pool()
        aw_data = {"AAPL": {"mentions": 50, "upvotes": 100, "rank": 1, "raw_score": 0.8},
                   "MSFT": {"mentions": 30, "upvotes": 60, "rank": 3, "raw_score": 0.6}}

        with patch("src.data.social_sentiment.fetch_apewisdom", new_callable=AsyncMock, return_value=aw_data):
            with patch("src.data.social_sentiment.fetch_fear_greed_index", new_callable=AsyncMock, return_value=None):
                with patch("src.data.social_sentiment.fetch_vix", new_callable=AsyncMock, return_value={"raw_level": 28.0, "normalized": 0.1}):
                    results = await run_social_sentiment(pool, ["AAPL", "MSFT"])
                    assert len(results) == 2
                    assert "AAPL" in results
                    assert "MSFT" in results

    @pytest.mark.asyncio
    async def test_stores_apewisdom_and_vix(self):
        pool, conn = _mock_pool()
        aw_data = {"AAPL": {"mentions": 40, "upvotes": 80, "rank": 2, "raw_score": 0.7}}

        with patch("src.data.social_sentiment.fetch_apewisdom", new_callable=AsyncMock, return_value=aw_data):
            with patch("src.data.social_sentiment.fetch_fear_greed_index", new_callable=AsyncMock, return_value=None):
                with patch("src.data.social_sentiment.fetch_vix", new_callable=AsyncMock, return_value={"raw_level": 28.0, "normalized": 0.1}):
                    await run_social_sentiment(pool, ["AAPL"])
                    # / should store both apewisdom and vix
                    assert conn.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_handles_fetch_failure_gracefully(self):
        pool, _conn = _mock_pool()

        with patch("src.data.social_sentiment.fetch_apewisdom", new_callable=AsyncMock, return_value={}):
            with patch("src.data.social_sentiment.fetch_fear_greed_index", new_callable=AsyncMock, return_value=None):
                with patch("src.data.social_sentiment.fetch_vix", new_callable=AsyncMock, return_value=None):
                    results = await run_social_sentiment(pool, ["AAPL"])
                    assert results["AAPL"] == 0.0

    @pytest.mark.asyncio
    async def test_symbol_not_in_apewisdom_gets_fear_gauge_only(self):
        pool, _conn = _mock_pool()

        with patch("src.data.social_sentiment.fetch_apewisdom", new_callable=AsyncMock, return_value={}):
            with patch("src.data.social_sentiment.fetch_fear_greed_index", new_callable=AsyncMock, return_value=None):
                with patch("src.data.social_sentiment.fetch_vix", new_callable=AsyncMock, return_value={"raw_level": 24.0, "normalized": 0.3}):
                    results = await run_social_sentiment(pool, ["AAPL"])
                    # / no apewisdom data, vix only: 0.3
                    assert results["AAPL"] == pytest.approx(0.3)

    @pytest.mark.asyncio
    async def test_empty_symbols_list(self):
        pool, _conn = _mock_pool()
        with patch("src.data.social_sentiment.fetch_apewisdom", new_callable=AsyncMock, return_value={}):
            with patch("src.data.social_sentiment.fetch_vix", new_callable=AsyncMock, return_value=None):
                with patch("src.data.social_sentiment.fetch_fear_greed_index", new_callable=AsyncMock, return_value=None):
                    results = await run_social_sentiment(pool, [])
                    assert results == {}

    @pytest.mark.asyncio
    async def test_apewisdom_fetched_once_for_stocks(self):
        pool, _conn = _mock_pool()
        aw_data = {"AAPL": {"mentions": 10, "upvotes": 20, "rank": 5, "raw_score": 0.4}}

        with patch("src.data.social_sentiment.fetch_apewisdom", new_callable=AsyncMock, return_value=aw_data) as mock_aw:
            with patch("src.data.social_sentiment.fetch_fear_greed_index", new_callable=AsyncMock, return_value=None):
                with patch("src.data.social_sentiment.fetch_vix", new_callable=AsyncMock, return_value=None):
                    await run_social_sentiment(pool, ["AAPL", "MSFT", "GOOG"])
                    # / apewisdom called twice: once for all-stocks, once for all-crypto
                    assert mock_aw.call_count == 2

    @pytest.mark.asyncio
    async def test_score_computation_matches(self):
        pool, _conn = _mock_pool()
        aw_data = {"AAPL": {"mentions": 100, "upvotes": 200, "rank": 1, "raw_score": 0.6}}

        with patch("src.data.social_sentiment.fetch_apewisdom", new_callable=AsyncMock, return_value=aw_data):
            with patch("src.data.social_sentiment.fetch_fear_greed_index", new_callable=AsyncMock, return_value=None):
                with patch("src.data.social_sentiment.fetch_vix", new_callable=AsyncMock, return_value={"raw_level": 22.0, "normalized": 0.4}):
                    results = await run_social_sentiment(pool, ["AAPL"])
                    # / equity: (0.6 * 0.6 + 0.4 * 0.4) / 1.0 = 0.52
                    assert results["AAPL"] == pytest.approx(0.52)


# ---------------------------------------------------------------------------
# / vix fetch tests
# ---------------------------------------------------------------------------

class TestFetchVix:
    def test_vix_returns_normalized(self):
        # / VIX=20 -> (30-20)/20 = 0.5
        import pandas as pd
        mock_hist = pd.DataFrame({"Close": [20.0]})
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = mock_hist
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = _fetch_vix_sync()
        assert result["normalized"] == pytest.approx(0.5)
        assert result["raw_level"] == pytest.approx(20.0)

    def test_vix_extreme_high_clamped(self):
        # / VIX=60 -> (30-60)/20 = -1.5 -> clamped to -1.0
        import pandas as pd
        mock_hist = pd.DataFrame({"Close": [60.0]})
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = mock_hist
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = _fetch_vix_sync()
        assert result["normalized"] == -1.0
        assert result["raw_level"] == pytest.approx(60.0)

    def test_vix_extreme_low_clamped(self):
        # / VIX=5 -> (30-5)/20 = 1.25 -> clamped to 1.0
        import pandas as pd
        mock_hist = pd.DataFrame({"Close": [5.0]})
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = mock_hist
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = _fetch_vix_sync()
        assert result["normalized"] == 1.0
        assert result["raw_level"] == pytest.approx(5.0)

    def test_vix_empty_history_returns_none(self):
        import pandas as pd
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = _fetch_vix_sync()
        assert result is None

    def test_vix_exception_returns_none(self):
        with patch("yfinance.Ticker", side_effect=Exception("api down")):
            result = _fetch_vix_sync()
        assert result is None

    @pytest.mark.asyncio
    async def test_async_fetch_vix_wraps_sync(self):
        vix_dict = {"raw_level": 24.0, "normalized": 0.3}
        with patch("src.data.social_sentiment._fetch_vix_sync", return_value=vix_dict):
            result = await fetch_vix()
        assert result["normalized"] == pytest.approx(0.3)
        assert result["raw_level"] == pytest.approx(24.0)


# ---------------------------------------------------------------------------
# / apewisdom tests
# ---------------------------------------------------------------------------

class TestFetchApewisdom:
    @pytest.mark.asyncio
    async def test_returns_dict_keyed_by_ticker(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"ticker": "NVDA", "mentions": 500, "upvotes": 1000, "rank": 1},
                {"ticker": "AAPL", "mentions": 200, "upvotes": 400, "rank": 2},
            ]
        }
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_apewisdom("all-stocks")
        assert "NVDA" in result
        assert "AAPL" in result
        assert result["NVDA"]["mentions"] == 500
        assert result["NVDA"]["rank"] == 1

    @pytest.mark.asyncio
    async def test_score_normalized_log_scale(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"ticker": "TOP", "mentions": 1000, "upvotes": 2000, "rank": 1},
                {"ticker": "MID", "mentions": 100, "upvotes": 200, "rank": 5},
                {"ticker": "LOW", "mentions": 10, "upvotes": 20, "rank": 20},
            ]
        }
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_apewisdom("all-stocks")
        # / top ticker gets score ~1.0
        assert result["TOP"]["raw_score"] == pytest.approx(1.0, abs=0.01)
        # / lower mentions get proportionally lower scores
        assert result["MID"]["raw_score"] < result["TOP"]["raw_score"]
        assert result["LOW"]["raw_score"] < result["MID"]["raw_score"]
        # / all scores between 0 and 1
        for t in result.values():
            assert 0.0 <= t["raw_score"] <= 1.0

    @pytest.mark.asyncio
    async def test_empty_results_returns_empty_dict(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": []}
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_apewisdom("all-stocks")
        assert result == {}

    @pytest.mark.asyncio
    async def test_api_error_returns_empty_dict(self):
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, side_effect=Exception("timeout")):
            result = await fetch_apewisdom("all-stocks")
        assert result == {}

    @pytest.mark.asyncio
    async def test_ticker_uppercased(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [{"ticker": "nvda", "mentions": 100, "upvotes": 200, "rank": 1}]
        }
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await fetch_apewisdom("all-stocks")
        assert "NVDA" in result

    @pytest.mark.asyncio
    async def test_crypto_filter(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"ticker": "BTC", "mentions": 800, "upvotes": 1600, "rank": 1},
                {"ticker": "ETH", "mentions": 400, "upvotes": 800, "rank": 2},
            ]
        }
        with patch("src.data.social_sentiment.api_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            result = await fetch_apewisdom("all-crypto")
        assert "BTC" in result
        assert "ETH" in result
        url = mock_get.call_args[0][0]
        assert "all-crypto" in url


# ---------------------------------------------------------------------------
# / stocktwits btc.x mapping tests (kept as fallback)
# ---------------------------------------------------------------------------

class TestStockTwitsCryptoMapping:
    @pytest.mark.asyncio
    async def test_btc_usd_maps_to_btc_x(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "symbol": {"id": 1},
            "sentiment": {"bullish": 60, "bearish": 40},
            "cursor": {"since": 0, "max": 0},
            "messages": [{"id": 1}, {"id": 2}, {"id": 3}],
        }
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False
        mock_client.get.return_value = mock_response

        with patch("httpx.AsyncClient", return_value=mock_client):
            await fetch_stocktwits_sentiment("BTC-USD")
        url = mock_client.get.call_args[0][0]
        assert "BTC.X" in url

    @pytest.mark.asyncio
    async def test_eth_usd_maps_to_eth_x(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "symbol": {"id": 1},
            "sentiment": {"bullish": 50, "bearish": 50},
            "cursor": {"since": 0, "max": 0},
            "messages": [],
        }
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False
        mock_client.get.return_value = mock_response

        with patch("httpx.AsyncClient", return_value=mock_client):
            await fetch_stocktwits_sentiment("ETH-USD")
        url = mock_client.get.call_args[0][0]
        assert "ETH.X" in url

    @pytest.mark.asyncio
    async def test_equity_symbol_unchanged(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "symbol": {"id": 1},
            "sentiment": {"bullish": 70, "bearish": 30},
            "cursor": {"since": 0, "max": 0},
            "messages": [{"id": 1}],
        }
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False
        mock_client.get.return_value = mock_response

        with patch("httpx.AsyncClient", return_value=mock_client):
            await fetch_stocktwits_sentiment("AAPL")
        url = mock_client.get.call_args[0][0]
        assert "AAPL" in url
        assert ".X" not in url


# ---------------------------------------------------------------------------
# / equity vs crypto score split
# ---------------------------------------------------------------------------

class TestScoreSplit:
    @pytest.mark.asyncio
    async def test_equity_uses_vix_not_fng(self):
        pool, _conn = _mock_pool()
        aw_data = {"AAPL": {"mentions": 10, "upvotes": 20, "rank": 5, "raw_score": 0.0}}
        fng_data = {"raw_value": 90.0, "normalized": 0.8}

        with patch("src.data.social_sentiment.fetch_apewisdom", new_callable=AsyncMock, return_value=aw_data):
            with patch("src.data.social_sentiment.fetch_fear_greed_index", new_callable=AsyncMock, return_value=fng_data):
                with patch("src.data.social_sentiment.fetch_vix", new_callable=AsyncMock, return_value={"raw_level": 40.0, "normalized": -0.5}):
                    results = await run_social_sentiment(pool, ["AAPL"])
                    # / equity: (0.0 * 0.6 + (-0.5) * 0.4) / 1.0 = -0.2
                    assert results["AAPL"] == pytest.approx(-0.2)

    @pytest.mark.asyncio
    async def test_crypto_uses_fng_not_vix(self):
        pool, _conn = _mock_pool()
        aw_crypto = {"BTC": {"mentions": 10, "upvotes": 20, "rank": 5, "raw_score": 0.0}}
        fng_data = {"raw_value": 90.0, "normalized": 0.8}

        async def aw_side_effect(filter_type):
            if filter_type == "all-crypto":
                return aw_crypto
            return {}

        with patch("src.data.social_sentiment.fetch_apewisdom", side_effect=aw_side_effect):
            with patch("src.data.social_sentiment.fetch_fear_greed_index", new_callable=AsyncMock, return_value=fng_data):
                with patch("src.data.social_sentiment.fetch_vix", new_callable=AsyncMock, return_value={"raw_level": 40.0, "normalized": -0.5}):
                    results = await run_social_sentiment(pool, ["BTC-USD"])
                    # / crypto: (0.0 * 0.6 + 0.8 * 0.4) / 1.0 = 0.32
                    assert results["BTC-USD"] == pytest.approx(0.32)

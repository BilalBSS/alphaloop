# / tests for market data fetching, parsing, validation, storage, backfill

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.data.market_data import (
    _parse_bar,
    backfill,
    fetch_bars,
    fetch_bars_alpaca,
    fetch_bars_yfinance,
    fetch_latest_quote,
    store_bars,
    store_intraday_bars,
    store_latest_prices,
)


def _mock_pool(mock_conn):
    # / helper to create a properly mocked asyncpg pool
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    mock_pool = MagicMock()
    mock_pool.acquire.return_value = mock_ctx
    return mock_pool


class TestParseBar:
    def test_parses_alpaca_bar(self):
        bar = {
            "t": "2024-01-15T05:00:00Z",
            "o": 150.0,
            "h": 155.0,
            "l": 149.0,
            "c": 153.5,
            "v": 1000000,
            "vw": 152.3,
        }
        result = _parse_bar("AAPL", bar)
        assert result["symbol"] == "AAPL"
        assert result["date"] == date(2024, 1, 15)
        assert result["open"] == Decimal("150.0")
        assert result["high"] == Decimal("155.0")
        assert result["low"] == Decimal("149.0")
        assert result["close"] == Decimal("153.5")
        assert result["volume"] == 1000000
        assert result["vwap"] == Decimal("152.3")

    def test_parses_bar_without_vwap(self):
        bar = {"t": "2024-01-15T05:00:00Z", "o": 100, "h": 110, "l": 90, "c": 105, "v": 500}
        result = _parse_bar("SPY", bar)
        assert result["vwap"] is None

    def test_parses_bar_without_volume(self):
        bar = {"t": "2024-01-15T05:00:00Z", "o": 100, "h": 110, "l": 90, "c": 105}
        result = _parse_bar("SPY", bar)
        assert result["volume"] == 0

    def test_returns_none_for_unparseable_timestamp(self):
        bar = {"t": "not-a-date", "o": 100, "h": 110, "l": 90, "c": 105, "v": 500}
        result = _parse_bar("SPY", bar)
        assert result is None

    def test_parses_crypto_timestamp_with_offset_suffix(self):
        # / crypto bars may come with +00:00 instead of Z
        bar = {"t": "2024-03-10T00:00:00+00:00", "o": 69000, "h": 70000, "l": 68000, "c": 69500, "v": 42}
        result = _parse_bar("BTC-USD", bar)
        assert result is not None
        assert result["date"] == date(2024, 3, 10)
        assert result["symbol"] == "BTC-USD"

    def test_bar_with_zero_volume_defaults_to_zero(self):
        bar = {"t": "2024-01-15T05:00:00Z", "o": 100, "h": 110, "l": 90, "c": 105, "v": 0}
        result = _parse_bar("SPY", bar)
        assert result["volume"] == 0

    def test_bar_with_negative_price_fields_parses(self):
        # / parse_bar does not validate prices — validation catches later
        bar = {"t": "2024-01-15T05:00:00Z", "o": -5, "h": 110, "l": -10, "c": 105, "v": 500}
        result = _parse_bar("SPY", bar)
        assert result is not None
        assert result["open"] == Decimal("-5")
        assert result["low"] == Decimal("-10")

    def test_returns_none_for_integer_timestamp(self):
        # / non-string timestamp (e.g. unix epoch int) should return None
        bar = {"t": 1705276800, "o": 100, "h": 110, "l": 90, "c": 105, "v": 500}
        result = _parse_bar("SPY", bar)
        assert result is None


class TestFetchBarsAlpaca:
    @pytest.mark.asyncio
    async def test_fetches_equity_bars(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "bars": [
                {"t": "2024-01-15T05:00:00Z", "o": 100, "h": 110, "l": 90, "c": 105, "v": 1000, "vw": 102},
            ],
            "next_page_token": None,
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch("src.data.market_data.get_alpaca_client", return_value=mock_client):
            with patch("src.data.market_data._rate_delay", 0):
                bars = await fetch_bars_alpaca("AAPL", date(2024, 1, 1), date(2024, 1, 31))

        assert len(bars) == 1
        assert bars[0]["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_fetches_crypto_bars(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "bars": {
                "BTC/USD": [
                    {"t": "2024-01-15T05:00:00Z", "o": 42000, "h": 43000, "l": 41000, "c": 42500, "v": 100},
                ]
            },
            "next_page_token": None,
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch("src.data.market_data.get_alpaca_client", return_value=mock_client):
            with patch("src.data.market_data._rate_delay", 0):
                bars = await fetch_bars_alpaca("BTC-USD", date(2024, 1, 1), date(2024, 1, 31))

        assert len(bars) == 1
        assert bars[0]["symbol"] == "BTC-USD"

    @pytest.mark.asyncio
    async def test_paginates(self):
        page1 = MagicMock()
        page1.raise_for_status = MagicMock()
        page1.json.return_value = {
            "bars": [{"t": "2024-01-15T05:00:00Z", "o": 100, "h": 110, "l": 90, "c": 105, "v": 1000, "vw": 102}],
            "next_page_token": "token123",
        }
        page2 = MagicMock()
        page2.raise_for_status = MagicMock()
        page2.json.return_value = {
            "bars": [{"t": "2024-01-16T05:00:00Z", "o": 105, "h": 115, "l": 100, "c": 110, "v": 2000, "vw": 108}],
            "next_page_token": None,
        }

        mock_client = AsyncMock()
        mock_client.get.side_effect = [page1, page2]

        with patch("src.data.market_data.get_alpaca_client", return_value=mock_client):
            with patch("src.data.market_data._rate_delay", 0):
                bars = await fetch_bars_alpaca("AAPL", date(2024, 1, 1), date(2024, 1, 31))

        assert len(bars) == 2


class TestFetchBarsYfinance:
    @pytest.mark.asyncio
    async def test_returns_empty_on_empty_dataframe(self):
        mock_yf = MagicMock()
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = None
        mock_yf.Ticker.return_value = mock_ticker

        with patch.dict("sys.modules", {"yfinance": mock_yf}):
            bars = await fetch_bars_yfinance("AAPL", date(2024, 1, 1), date(2024, 1, 31))
            assert bars == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_fetch_error(self):
        mock_yf = MagicMock()
        mock_ticker = MagicMock()
        mock_ticker.history.side_effect = Exception("network error")
        mock_yf.Ticker.return_value = mock_ticker

        with patch.dict("sys.modules", {"yfinance": mock_yf}):
            bars = await fetch_bars_yfinance("AAPL", date(2024, 1, 1), date(2024, 1, 31))
            assert bars == []


class TestFetchBars:
    @pytest.mark.asyncio
    async def test_falls_back_to_yfinance_on_alpaca_failure(self):
        yf_bars = [{"symbol": "AAPL", "date": date(2024, 1, 15), "open": Decimal("100"),
                     "high": Decimal("110"), "low": Decimal("90"), "close": Decimal("105"),
                     "volume": 1000, "vwap": None}]

        with patch("src.data.market_data.fetch_bars_alpaca", side_effect=Exception("api error")):
            with patch("src.data.market_data.fetch_bars_yfinance", return_value=yf_bars) as mock_yf:
                bars = await fetch_bars("AAPL", date(2024, 1, 1), date(2024, 1, 31))
                assert len(bars) == 1
                mock_yf.assert_called_once()

    @pytest.mark.asyncio
    async def test_falls_back_when_alpaca_returns_empty(self):
        with patch("src.data.market_data.fetch_bars_alpaca", return_value=[]):
            with patch("src.data.market_data.fetch_bars_yfinance", return_value=[]) as mock_yf:
                bars = await fetch_bars("AAPL", date(2024, 1, 1), date(2024, 1, 31))
                assert bars == []
                mock_yf.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_alpaca_when_successful(self):
        alpaca_bars = [{"symbol": "AAPL", "date": date(2024, 1, 15)}]

        with patch("src.data.market_data.fetch_bars_alpaca", return_value=alpaca_bars):
            with patch("src.data.market_data.fetch_bars_yfinance") as mock_yf:
                bars = await fetch_bars("AAPL", date(2024, 1, 1), date(2024, 1, 31))
                assert len(bars) == 1
                mock_yf.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_alpaca_equity_env_bypasses_alpaca(self):
        # / SKIP_ALPACA_EQUITY_BARS=true goes straight to yfinance for debugging
        yf_bars = [{"symbol": "AAPL", "date": date(2024, 1, 15)}]
        with patch.dict(os.environ, {"SKIP_ALPACA_EQUITY_BARS": "true"}):
            with patch("src.data.market_data.fetch_bars_alpaca") as mock_alpaca:
                with patch("src.data.market_data.fetch_bars_yfinance", return_value=yf_bars) as mock_yf:
                    bars = await fetch_bars("AAPL", date(2024, 1, 1), date(2024, 1, 31))
                    assert bars == yf_bars
                    mock_alpaca.assert_not_called()
                    mock_yf.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_alpaca_equity_does_not_bypass_crypto(self):
        # / crypto path has no 403, the skip flag should not apply
        alpaca_bars = [{"symbol": "BTC-USD", "date": date(2024, 1, 15)}]
        with patch.dict(os.environ, {"SKIP_ALPACA_EQUITY_BARS": "true"}):
            with patch("src.data.market_data.fetch_bars_alpaca", return_value=alpaca_bars) as mock_alpaca:
                bars = await fetch_bars("BTC-USD", date(2024, 1, 1), date(2024, 1, 31))
                assert bars == alpaca_bars
                mock_alpaca.assert_called_once()


class TestFeedSelection:
    def test_default_equity_feed_is_iex_for_paper(self):
        # / paper base url forces iex to avoid sip-only 403 on paper-tier keys
        from src.data.market_data import _default_equity_feed
        with patch.dict(os.environ, {"ALPACA_BASE_URL": "https://paper-api.alpaca.markets"}):
            assert _default_equity_feed() == "iex"

    def test_default_equity_feed_is_none_for_live(self):
        from src.data.market_data import _default_equity_feed
        with patch.dict(os.environ, {"ALPACA_BASE_URL": "https://api.alpaca.markets"}, clear=False):
            # / live base url → no forced feed, caller's account entitlement decides
            assert _default_equity_feed() is None

    def test_skip_alpaca_equity_respects_env_casing(self):
        from src.data.market_data import _should_skip_alpaca_equity
        for val in ("true", "TRUE", "1", "yes"):
            with patch.dict(os.environ, {"SKIP_ALPACA_EQUITY_BARS": val}):
                assert _should_skip_alpaca_equity() is True
        for val in ("false", "0", "no", ""):
            with patch.dict(os.environ, {"SKIP_ALPACA_EQUITY_BARS": val}):
                assert _should_skip_alpaca_equity() is False


class TestStoreBars:
    @pytest.mark.asyncio
    async def test_stores_valid_bars(self):
        bars = [
            {"symbol": "AAPL", "date": date(2024, 1, 15),
             "open": Decimal("100"), "high": Decimal("110"),
             "low": Decimal("90"), "close": Decimal("105"),
             "volume": 1000, "vwap": Decimal("102")},
        ]

        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)

        count = await store_bars(pool, bars)
        assert count == 1
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_invalid_bars(self):
        bars = [
            {"symbol": "BAD", "date": date(2024, 1, 15),
             "open": Decimal("-999"), "high": Decimal("110"),
             "low": Decimal("90"), "close": Decimal("105"),
             "volume": 1000, "vwap": None},
        ]

        mock_pool = AsyncMock()
        count = await store_bars(mock_pool, bars)
        assert count == 0

    @pytest.mark.asyncio
    async def test_empty_bars_returns_zero(self):
        mock_pool = AsyncMock()
        count = await store_bars(mock_pool, [])
        assert count == 0

    @pytest.mark.asyncio
    async def test_continues_on_insert_error(self):
        bars = [
            {"symbol": "AAPL", "date": date(2024, 1, 15),
             "open": Decimal("100"), "high": Decimal("110"),
             "low": Decimal("90"), "close": Decimal("105"),
             "volume": 1000, "vwap": None},
            {"symbol": "MSFT", "date": date(2024, 1, 15),
             "open": Decimal("200"), "high": Decimal("210"),
             "low": Decimal("190"), "close": Decimal("205"),
             "volume": 2000, "vwap": None},
        ]

        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = [Exception("db error"), None]
        pool = _mock_pool(mock_conn)

        count = await store_bars(pool, bars)
        # / first fails, second succeeds
        assert count == 1

    @pytest.mark.asyncio
    async def test_upsert_overwrites_existing_bar(self):
        # / store_bars uses ON CONFLICT ... DO UPDATE — verify execute is called with that sql
        bars = [
            {"symbol": "AAPL", "date": date(2024, 1, 15),
             "open": Decimal("150"), "high": Decimal("160"),
             "low": Decimal("145"), "close": Decimal("155"),
             "volume": 3000, "vwap": Decimal("152")},
        ]

        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)

        count = await store_bars(pool, bars)
        assert count == 1
        # / verify the sql contains ON CONFLICT for upsert behavior
        call_args = mock_conn.execute.call_args
        sql = call_args[0][0]
        assert "ON CONFLICT" in sql
        assert "DO UPDATE" in sql

    @pytest.mark.asyncio
    async def test_mixed_valid_and_invalid_bars_counts_only_valid(self):
        bars = [
            # / valid bar
            {"symbol": "AAPL", "date": date(2024, 1, 15),
             "open": Decimal("100"), "high": Decimal("110"),
             "low": Decimal("90"), "close": Decimal("105"),
             "volume": 1000, "vwap": None},
            # / invalid bar: negative open fails validation
            {"symbol": "BAD", "date": date(2024, 1, 15),
             "open": Decimal("-999"), "high": Decimal("110"),
             "low": Decimal("90"), "close": Decimal("105"),
             "volume": 1000, "vwap": None},
            # / valid bar
            {"symbol": "MSFT", "date": date(2024, 1, 15),
             "open": Decimal("200"), "high": Decimal("210"),
             "low": Decimal("190"), "close": Decimal("205"),
             "volume": 2000, "vwap": None},
        ]

        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)

        count = await store_bars(pool, bars)
        # / only 2 valid bars inserted, 1 invalid skipped
        assert count == 2
        assert mock_conn.execute.call_count == 2


class TestBackfill:
    @pytest.mark.asyncio
    async def test_incremental_backfill_skips_up_to_date(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"max_date": date.today()}
        pool = _mock_pool(mock_conn)

        with patch("src.data.market_data.fetch_bars") as mock_fetch:
            results = await backfill(pool, ["AAPL"], years=5)
            assert results["AAPL"] == 0
            mock_fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_backfill_fetches_from_scratch(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"max_date": None}
        pool = _mock_pool(mock_conn)

        bars = [{"symbol": "AAPL", "date": date(2024, 1, 15),
                 "open": Decimal("100"), "high": Decimal("110"),
                 "low": Decimal("90"), "close": Decimal("105"),
                 "volume": 1000, "vwap": None}]

        with patch("src.data.market_data.fetch_bars", return_value=bars):
            with patch("src.data.market_data.store_bars", return_value=1) as mock_store:
                results = await backfill(pool, ["AAPL"], years=5)
                assert results["AAPL"] == 1
                mock_store.assert_called_once()

    @pytest.mark.asyncio
    async def test_backfill_continues_on_symbol_failure(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"max_date": None}
        pool = _mock_pool(mock_conn)

        call_count = 0

        async def mock_fetch(sym, start, end):
            nonlocal call_count
            call_count += 1
            if sym == "AAPL":
                raise Exception("api error")
            return [{"symbol": sym, "date": date(2024, 1, 15),
                     "open": Decimal("100"), "high": Decimal("110"),
                     "low": Decimal("90"), "close": Decimal("105"),
                     "volume": 1000, "vwap": None}]

        with patch("src.data.market_data.fetch_bars", side_effect=mock_fetch):
            with patch("src.data.market_data.store_bars", return_value=1):
                results = await backfill(pool, ["AAPL", "MSFT"], years=5)
                assert results["AAPL"] == 0
                assert results["MSFT"] == 1

    @pytest.mark.asyncio
    async def test_incremental_backfill_uses_correct_start_date(self):
        # / when data exists, fetch_start = max_date + 1 day
        existing_max = date(2024, 6, 15)
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"max_date": existing_max}
        pool = _mock_pool(mock_conn)

        with patch("src.data.market_data.fetch_bars", return_value=[]) as mock_fetch:
            with patch("src.data.market_data.store_bars", return_value=0):
                await backfill(pool, ["AAPL"], years=5)
                # / should have been called with start = max_date + 1
                call_args = mock_fetch.call_args
                fetch_start = call_args[0][1]
                assert fetch_start == existing_max + timedelta(days=1)

    @pytest.mark.asyncio
    async def test_years_parameter_affects_date_range(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"max_date": None}
        pool = _mock_pool(mock_conn)

        with patch("src.data.market_data.fetch_bars", return_value=[]) as mock_fetch:
            with patch("src.data.market_data.store_bars", return_value=0):
                await backfill(pool, ["AAPL"], years=2)
                call_args = mock_fetch.call_args
                fetch_start = call_args[0][1]
                fetch_end = call_args[0][2]
                # / years=2 means start = today - 2*365
                expected_start = date.today() - timedelta(days=2 * 365)
                assert fetch_start == expected_start
                assert fetch_end == date.today()


class TestFetchLatestQuote:
    @pytest.mark.asyncio
    async def test_fetches_equity_quote(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "trade": {"p": 150.25, "t": "2024-01-15T15:30:00Z"}
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch("src.data.market_data.get_alpaca_client", return_value=mock_client):
            result = await fetch_latest_quote("AAPL")
            assert result["symbol"] == "AAPL"
            assert result["price"] == Decimal("150.25")

    @pytest.mark.asyncio
    async def test_fetches_crypto_quote(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "trades": {"BTC/USD": {"p": 42000.50, "t": "2024-01-15T15:30:00Z"}}
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch("src.data.market_data.get_alpaca_client", return_value=mock_client):
            result = await fetch_latest_quote("BTC-USD")
            assert result["symbol"] == "BTC-USD"
            assert result["price"] == Decimal("42000.50")


class TestParseBarTimestamp:
    def test_parse_bar_includes_timestamp(self):
        bar = {"t": "2024-03-15T14:00:00Z", "o": 150, "h": 155, "l": 148, "c": 152, "v": 1000}
        result = _parse_bar("AAPL", bar)
        assert result["timestamp"] is not None
        assert result["timestamp"].year == 2024
        assert result["timestamp"].hour == 14

    def test_timestamp_and_date_consistent(self):
        bar = {"t": "2024-06-20T10:00:00Z", "o": 100, "h": 105, "l": 98, "c": 103, "v": 500}
        result = _parse_bar("MSFT", bar)
        assert result["date"] == result["timestamp"].date()


class TestStoreIntradayBars:
    @pytest.mark.asyncio
    async def test_stores_bars_with_upsert(self):
        from datetime import datetime, timezone
        bars = [{
            "symbol": "AAPL",
            "timestamp": datetime(2024, 3, 15, 14, 0, tzinfo=timezone.utc),
            "open": Decimal("150.00"), "high": Decimal("155.00"),
            "low": Decimal("148.00"), "close": Decimal("152.00"),
            "volume": 10000, "vwap": Decimal("151.50"),
        }]
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        count = await store_intraday_bars(pool, bars, timeframe="1Hour")
        assert count == 1
        sql = mock_conn.execute.call_args[0][0]
        assert "market_data_intraday" in sql
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_empty_bars_returns_zero(self):
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        count = await store_intraday_bars(pool, [])
        assert count == 0
        mock_conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_continues_on_insert_error(self):
        from datetime import datetime, timezone
        bars = [
            {"symbol": "AAPL", "timestamp": datetime(2024, 3, 15, 14, 0, tzinfo=timezone.utc),
             "open": Decimal("150"), "high": Decimal("155"), "low": Decimal("148"),
             "close": Decimal("152"), "volume": 1000, "vwap": None},
            {"symbol": "AAPL", "timestamp": datetime(2024, 3, 15, 16, 0, tzinfo=timezone.utc),
             "open": Decimal("152"), "high": Decimal("157"), "low": Decimal("150"),
             "close": Decimal("155"), "volume": 2000, "vwap": None},
        ]
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = [Exception("db error"), None]
        pool = _mock_pool(mock_conn)
        count = await store_intraday_bars(pool, bars)
        assert count == 1


# -- store_latest_prices (bug 2a: stop polluting market_data_intraday) --

class TestStoreLatestPrices:
    @pytest.mark.asyncio
    async def test_upserts_prices_into_latest_prices_table(self):
        # / bug 2a: price refresh must hit latest_prices, not market_data_intraday
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        count = await store_latest_prices(pool, {"AAPL": 175.43, "MSFT": 420.10})
        assert count == 2
        mock_conn.executemany.assert_called_once()
        sql = mock_conn.executemany.call_args.args[0]
        assert "INSERT INTO latest_prices" in sql
        assert "ON CONFLICT (symbol)" in sql
        # / never touch market_data_intraday in this path
        assert "market_data_intraday" not in sql

    @pytest.mark.asyncio
    async def test_coerces_float_to_decimal(self):
        # / asyncpg rejects python float on DECIMAL columns — must be Decimal
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        await store_latest_prices(pool, {"BTC-USD": 42000.5})
        rows = mock_conn.executemany.call_args.args[1]
        assert len(rows) == 1
        assert rows[0][0] == "BTC-USD"
        assert isinstance(rows[0][1], Decimal)
        assert rows[0][1] == Decimal("42000.5")

    @pytest.mark.asyncio
    async def test_empty_dict_noop(self):
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)
        count = await store_latest_prices(pool, {})
        assert count == 0
        mock_conn.executemany.assert_not_called()

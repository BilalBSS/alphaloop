# / tests for sec filings: insider trades fetch, parse, store, rate limiting

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from src.data.sec_filings import (
    _get_transactions,
    _get_user_agent,
    _safe_get,
    fetch_all_insider_trades,
    fetch_insider_trades,
    store_insider_trades,
)


def _mock_pool(mock_conn):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


class TestSafeGet:
    def test_gets_existing_attr(self):
        obj = MagicMock()
        obj.name = "test"
        assert _safe_get(obj, "name") == "test"

    def test_returns_default_for_missing_attr(self):
        obj = MagicMock(spec=[])
        assert _safe_get(obj, "missing", "default") == "default"

    def test_returns_default_for_none_value(self):
        obj = MagicMock()
        obj.name = None
        assert _safe_get(obj, "name", "fallback") == "fallback"

    def test_returns_default_on_exception(self):
        obj = MagicMock()
        type(obj).name = property(lambda self: (_ for _ in ()).throw(RuntimeError))
        assert _safe_get(obj, "name", "safe") == "safe"

    def test_returns_numeric_attribute_value(self):
        obj = MagicMock()
        obj.count = 42
        assert _safe_get(obj, "count") == 42

    def test_returns_list_attribute_value(self):
        obj = MagicMock()
        obj.items = [1, 2, 3]
        assert _safe_get(obj, "items") == [1, 2, 3]


class TestGetUserAgent:
    def test_returns_env_var_when_set(self):
        with patch.dict("os.environ", {"SEC_EDGAR_USER_AGENT": "MyAgent my@email.com"}):
            assert _get_user_agent() == "MyAgent my@email.com"

    def test_returns_default_when_not_set(self):
        with patch.dict("os.environ", {}, clear=True):
            result = _get_user_agent()
            assert "QuantTrader" in result


class TestGetTransactions:
    # / v5 api: _get_transactions uses market_trades (dataframe) and non_derivative_table

    def _make_form4(self, market_rows=None, ndt_rows=None):
        # / helper to build a v5 form4 mock with dataframe-based transaction data
        form4 = MagicMock()

        if market_rows is not None:
            form4.market_trades = pd.DataFrame(market_rows)
        else:
            form4.market_trades = None

        if ndt_rows is not None:
            ndt = MagicMock()
            ndt.has_transactions = True
            ndt.transactions.data = pd.DataFrame(ndt_rows)
            form4.non_derivative_table = ndt
        else:
            form4.non_derivative_table = None

        return form4

    def test_extracts_buy_transaction(self):
        form4 = self._make_form4(market_rows=[{"Code": "P", "Shares": 1000, "Price": 150.0}])
        txns = _get_transactions(form4)
        assert len(txns) == 1
        assert txns[0]["type"] == "buy"
        assert txns[0]["shares"] == 1000

    def test_extracts_sell_transaction(self):
        form4 = self._make_form4(market_rows=[{"Code": "S", "Shares": 500, "Price": 200.0}])
        txns = _get_transactions(form4)
        assert len(txns) == 1
        assert txns[0]["type"] == "sell"

    def test_extracts_option_exercise(self):
        # / option exercises come from non_derivative_table, not market_trades
        form4 = self._make_form4(ndt_rows=[{"Code": "M", "Shares": 2000, "Price": 50.0}])
        txns = _get_transactions(form4)
        assert len(txns) == 1
        assert txns[0]["type"] == "option_exercise"

    def test_handles_unknown_code(self):
        form4 = self._make_form4(market_rows=[{"Code": "Z", "Shares": 100, "Price": 10.0}])
        txns = _get_transactions(form4)
        assert txns[0]["type"] == "other"

    def test_handles_no_transactions(self):
        form4 = self._make_form4()
        txns = _get_transactions(form4)
        assert txns == []

    def test_continues_on_iteration_error(self):
        form4 = MagicMock()
        # / market_trades raises on iteration
        form4.market_trades = MagicMock()
        form4.market_trades.empty = False
        form4.market_trades.iterrows = MagicMock(side_effect=TypeError("not iterable"))
        form4.non_derivative_table = None
        txns = _get_transactions(form4)
        assert txns == []

    def test_multiple_transaction_types_in_single_form4(self):
        # / buy + sell in market_trades, option_exercise in non_derivative_table
        form4 = self._make_form4(
            market_rows=[
                {"Code": "P", "Shares": 1000, "Price": 100.0},
                {"Code": "S", "Shares": 500, "Price": 120.0},
            ],
            ndt_rows=[{"Code": "M", "Shares": 2000, "Price": 50.0}],
        )
        txns = _get_transactions(form4)
        assert len(txns) == 3
        types = {t["type"] for t in txns}
        assert types == {"buy", "sell", "option_exercise"}

    def test_zero_shares_transaction(self):
        form4 = self._make_form4(market_rows=[{"Code": "P", "Shares": 0, "Price": 100.0}])
        txns = _get_transactions(form4)
        assert len(txns) == 1
        assert txns[0]["shares"] == 0

    def test_zero_price_transaction(self):
        form4 = self._make_form4(market_rows=[{"Code": "M", "Shares": 1000, "Price": 0}])
        txns = _get_transactions(form4)
        assert len(txns) == 1
        assert txns[0]["price"] == 0

    def test_lowercase_transaction_codes(self):
        # / lowercase p, s should map to buy, sell via _code_to_type
        for code, expected in [("p", "buy"), ("s", "sell")]:
            form4 = self._make_form4(market_rows=[{"Code": code, "Shares": 100, "Price": 50.0}])
            txns = _get_transactions(form4)
            assert txns[0]["type"] == expected

    def test_ndt_excludes_market_codes(self):
        # / non_derivative_table skips P and S codes (those are in market_trades)
        form4 = self._make_form4(ndt_rows=[
            {"Code": "P", "Shares": 100, "Price": 50.0},
            {"Code": "M", "Shares": 200, "Price": 30.0},
        ])
        txns = _get_transactions(form4)
        # / only M should come through, P is filtered out
        assert len(txns) == 1
        assert txns[0]["type"] == "option_exercise"


class TestFetchInsiderTrades:
    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        with patch("src.data.sec_filings._fetch_insider_trades_sync", side_effect=Exception("fail")):
            with patch("src.data.sec_filings._edgar_delay", 0):
                trades = await fetch_insider_trades("AAPL", days=90)
                assert trades == []

    @pytest.mark.asyncio
    async def test_returns_trades_on_success(self):
        mock_trades = [
            {"symbol": "AAPL", "filing_date": date.today(),
             "insider_name": "Tim Cook", "insider_title": "CEO",
             "transaction_type": "sell", "shares": Decimal("50000"),
             "price_per_share": Decimal("150"), "total_value": Decimal("7500000")},
        ]

        with patch("src.data.sec_filings._fetch_insider_trades_sync", return_value=mock_trades):
            with patch("src.data.sec_filings._edgar_delay", 0):
                trades = await fetch_insider_trades("AAPL", days=90)
                assert len(trades) == 1
                assert trades[0]["insider_name"] == "Tim Cook"


class TestFetchAllInsiderTrades:
    @pytest.mark.asyncio
    async def test_fetches_all_symbols(self):
        async def mock_fetch(sym, days=90):
            return [{"symbol": sym, "filing_date": date.today(),
                     "insider_name": "Insider", "insider_title": "",
                     "transaction_type": "buy", "shares": Decimal("100"),
                     "price_per_share": Decimal("50"), "total_value": Decimal("5000")}]

        with patch("src.data.sec_filings.fetch_insider_trades", side_effect=mock_fetch):
            trades = await fetch_all_insider_trades(["AAPL", "MSFT"], days=90)
            assert len(trades) == 2

    @pytest.mark.asyncio
    async def test_continues_when_symbol_returns_empty(self):
        async def mock_fetch(sym, days=90):
            if sym == "FAKE":
                return []
            return [{"symbol": sym, "filing_date": date.today(),
                     "insider_name": "Insider", "insider_title": "",
                     "transaction_type": "buy", "shares": Decimal("100"),
                     "price_per_share": Decimal("50"), "total_value": Decimal("5000")}]

        with patch("src.data.sec_filings.fetch_insider_trades", side_effect=mock_fetch):
            trades = await fetch_all_insider_trades(["AAPL", "FAKE", "MSFT"], days=90)
            assert len(trades) == 2


class TestStoreInsiderTrades:
    @pytest.mark.asyncio
    async def test_stores_trades(self):
        trades = [
            {"symbol": "AAPL", "filing_date": date.today(),
             "insider_name": "Tim Cook", "insider_title": "CEO",
             "transaction_type": "sell", "shares": Decimal("50000"),
             "price_per_share": Decimal("150"), "total_value": Decimal("7500000")},
        ]

        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)

        count = await store_insider_trades(pool, trades)
        assert count == 1
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_trades_returns_zero(self):
        mock_pool = AsyncMock()
        count = await store_insider_trades(mock_pool, [])
        assert count == 0

    @pytest.mark.asyncio
    async def test_continues_on_insert_error(self):
        trades = [
            {"symbol": "AAPL", "filing_date": date.today(),
             "insider_name": "A", "insider_title": "",
             "transaction_type": "buy", "shares": Decimal("100"),
             "price_per_share": Decimal("50"), "total_value": Decimal("5000")},
            {"symbol": "MSFT", "filing_date": date.today(),
             "insider_name": "B", "insider_title": "",
             "transaction_type": "sell", "shares": Decimal("200"),
             "price_per_share": Decimal("300"), "total_value": Decimal("60000")},
        ]

        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = [Exception("duplicate"), None]
        pool = _mock_pool(mock_conn)

        count = await store_insider_trades(pool, trades)
        # / first fails, second succeeds
        assert count == 1


# ---------------------------------------------------------------------------
# / _to_decimal_safe helper (bug 5b: nan/inf leaking into jsonb)
# ---------------------------------------------------------------------------

class TestToDecimalSafe:
    def test_none_returns_zero(self):
        from src.data.sec_filings import _to_decimal_safe
        assert _to_decimal_safe(None) == Decimal(0)

    def test_float_nan_returns_zero(self):
        # / bug 5b root cause: float('nan') serialized as literal "NaN" in jsonb
        import math

        from src.data.sec_filings import _to_decimal_safe
        assert _to_decimal_safe(float("nan")) == Decimal(0)
        assert _to_decimal_safe(math.nan) == Decimal(0)

    def test_float_inf_returns_zero(self):
        from src.data.sec_filings import _to_decimal_safe
        assert _to_decimal_safe(float("inf")) == Decimal(0)
        assert _to_decimal_safe(float("-inf")) == Decimal(0)

    def test_nan_string_returns_zero(self):
        # / Decimal('NaN') can also slip in from edgartools str coercion
        from src.data.sec_filings import _to_decimal_safe
        assert _to_decimal_safe("NaN") == Decimal(0)

    def test_valid_float_preserved(self):
        from src.data.sec_filings import _to_decimal_safe
        assert _to_decimal_safe(175.43) == Decimal("175.43")

    def test_valid_int_preserved(self):
        from src.data.sec_filings import _to_decimal_safe
        assert _to_decimal_safe(100) == Decimal(100)

    def test_unparseable_string_returns_zero(self):
        from src.data.sec_filings import _to_decimal_safe
        assert _to_decimal_safe("not a number") == Decimal(0)
        assert _to_decimal_safe("") == Decimal(0)

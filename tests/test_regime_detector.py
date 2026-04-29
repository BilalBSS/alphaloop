# / tests for regime detection: classification, indicators, backfill

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from src.data.regime_detector import (
    MIN_HISTORY_DAYS,
    _drawdown_from_high,
    _median_volatility,
    _rolling_volatility,
    _sma,
    backfill_regimes,
    classify_regimes,
    classify_single_date,
    snapshot_regime_daily,
)


def _mock_pool(mock_conn):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


class TestRollingVolatility:
    def test_returns_zero_for_insufficient_data(self):
        prices = np.array([100.0, 101.0])
        assert _rolling_volatility(prices, 20) == 0.0

    def test_computes_positive_volatility(self):
        # / 50 prices with some variation
        np.random.seed(42)
        prices = np.cumsum(np.random.randn(50)) + 100
        prices = np.abs(prices)  # / ensure positive
        vol = _rolling_volatility(prices, 20)
        assert vol > 0

    def test_zero_volatility_for_flat_prices(self):
        prices = np.full(50, 100.0)
        vol = _rolling_volatility(prices, 20)
        assert vol == 0.0

    def test_annualized(self):
        # / volatility should be annualized (multiplied by sqrt(252))
        np.random.seed(42)
        prices = np.cumsum(np.random.randn(50) * 0.01) + 100
        prices = np.abs(prices)
        vol = _rolling_volatility(prices, 20)
        # / annualized vol should be much larger than daily std
        log_returns = np.diff(np.log(prices[-21:]))
        daily_std = np.std(log_returns)
        assert vol > daily_std * 10  # / sqrt(252) ~ 15.87

    def test_with_exactly_window_plus_one_data_points(self):
        # / minimum for valid output: window + 1 prices
        np.random.seed(42)
        prices = np.cumsum(np.random.randn(21) * 0.01) + 100
        prices = np.abs(prices)
        vol = _rolling_volatility(prices, 20)
        assert vol > 0

    def test_returns_annualized_value_sqrt_252(self):
        # / verify vol = std(log_returns) * sqrt(252)
        np.random.seed(42)
        prices = np.cumsum(np.random.randn(50) * 0.01) + 100
        prices = np.abs(prices)
        vol = _rolling_volatility(prices, 20)
        log_returns = np.diff(np.log(prices[-21:]))
        expected = float(np.std(log_returns) * np.sqrt(252))
        assert abs(vol - expected) < 1e-10

    def test_known_volatility_constant_daily_returns(self):
        # / constant 1% daily returns: all log returns identical -> std ~ 0
        prices = [100.0]
        for _ in range(30):
            prices.append(prices[-1] * 1.01)
        prices = np.array(prices)
        vol = _rolling_volatility(prices, 20)
        # / all returns are identical so std is essentially 0 (float precision noise)
        assert vol < 1e-10


class TestSma:
    def test_returns_none_for_insufficient_data(self):
        prices = np.array([100.0, 101.0])
        assert _sma(prices, 50) is None

    def test_computes_correct_average(self):
        prices = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
        result = _sma(prices, 3)
        # / last 3: 30, 40, 50 -> avg = 40
        assert result == 40.0

    def test_uses_last_n_prices(self):
        prices = np.array([1.0, 2.0, 100.0, 100.0])
        result = _sma(prices, 2)
        assert result == 100.0

    def test_exact_calculation(self):
        # / [10, 20, 30, 40, 50] with period 5 -> mean of all 5 = 30.0
        prices = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
        result = _sma(prices, 5)
        assert result == 30.0


class TestDrawdownFromHigh:
    def test_zero_drawdown_at_peak(self):
        prices = np.array([90.0, 95.0, 100.0])
        dd = _drawdown_from_high(prices, 252)
        assert dd == 0.0

    def test_computes_drawdown(self):
        prices = np.array([100.0, 90.0, 80.0])
        dd = _drawdown_from_high(prices, 252)
        # / peak = 100, current = 80 -> dd = 0.2
        assert abs(dd - 0.2) < 0.001

    def test_empty_prices(self):
        prices = np.array([])
        dd = _drawdown_from_high(prices, 252)
        assert dd == 0.0

    def test_respects_lookback(self):
        # / old peak outside lookback should not count
        prices = np.array([200.0] + [100.0] * 300 + [95.0])
        dd = _drawdown_from_high(prices, 252)
        # / lookback of 252, peak within window = 100, current = 95
        assert abs(dd - 0.05) < 0.001

    def test_lookback_shorter_than_data(self):
        # / peak at 200 is outside lookback=3, so only recent data matters
        prices = np.array([200.0, 50.0, 60.0, 55.0])
        dd = _drawdown_from_high(prices, 3)
        # / window = [50, 60, 55], peak = 60, current = 55
        assert abs(dd - (60 - 55) / 60) < 0.001

    def test_single_element_array(self):
        prices = np.array([100.0])
        dd = _drawdown_from_high(prices, 252)
        # / single price = peak = current, so dd = 0
        assert dd == 0.0


class TestMedianVolatility:
    def test_returns_zero_for_insufficient_data(self):
        prices = np.array([100.0, 101.0])
        result = _median_volatility(prices, 252, 20)
        assert result == 0.0

    def test_returns_positive_for_sufficient_data(self):
        np.random.seed(42)
        prices = np.cumsum(np.random.randn(300) * 0.01) + 100
        prices = np.abs(prices)
        result = _median_volatility(prices, 252, 20)
        assert result > 0


class TestClassifyRegimes:
    def _make_bull_prices(self, n=300):
        # / steadily rising prices with low vol
        return [100.0 + i * 0.5 for i in range(n)]

    def _make_bear_prices(self, n=300):
        # / sharp decline: rise then fall
        prices = [100.0 + i * 0.5 for i in range(200)]
        # / drop 25% over remaining days
        peak = prices[-1]
        for i in range(n - 200):
            prices.append(peak * (1 - 0.25 * (i + 1) / (n - 200)))
        return prices

    def _make_high_vol_prices(self, n=300):
        # / extreme swings
        np.random.seed(42)
        prices = [100.0]
        for _i in range(1, n):
            prices.append(prices[-1] * (1 + np.random.randn() * 0.05))
            if prices[-1] < 10:
                prices[-1] = 10.0
        return prices

    def test_empty_input(self):
        results = classify_regimes([], [], "equity")
        assert results == []

    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError):
            classify_regimes([date.today()], [100.0, 200.0], "equity")

    def test_insufficient_data_for_early_dates(self):
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(50)]
        closes = [100.0 + i for i in range(50)]
        results = classify_regimes(dates, closes, "equity")
        assert all(r.regime == "insufficient_data" for r in results)

    def test_bull_regime_detected(self):
        n = 300
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        closes = self._make_bull_prices(n)
        results = classify_regimes(dates, closes, "equity")
        # / after sufficient data, should detect bull
        classified = [r for r in results if r.regime != "insufficient_data"]
        assert len(classified) > 0
        # / majority should be bull
        bull_count = sum(1 for r in classified if r.regime == "bull")
        assert bull_count / len(classified) > 0.5

    def test_bear_regime_detected(self):
        n = 300
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        closes = self._make_bear_prices(n)
        results = classify_regimes(dates, closes, "equity")
        classified = [r for r in results if r.regime != "insufficient_data"]
        # / last results should include bear or high_vol (deep drawdown triggers both)
        last_10 = classified[-10:]
        assert any(r.regime in ("bear", "high_vol") for r in last_10)

    def test_result_fields_populated(self):
        n = 250
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        closes = self._make_bull_prices(n)
        results = classify_regimes(dates, closes, "equity")
        classified = [r for r in results if r.regime != "insufficient_data"]
        assert len(classified) > 0

        r = classified[0]
        assert isinstance(r.date, date)
        assert r.market == "equity"
        assert r.regime in ("bull", "bear", "sideways", "high_vol")
        assert 0.0 <= r.confidence <= 1.0
        assert r.volatility_20d >= 0
        assert isinstance(r.sma50_above_200, bool)
        assert r.drawdown_from_high >= 0

    def test_crypto_market_label(self):
        n = 250
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        closes = self._make_bull_prices(n)
        results = classify_regimes(dates, closes, "crypto")
        classified = [r for r in results if r.regime != "insufficient_data"]
        assert all(r.market == "crypto" for r in classified)

    def test_sideways_market_detected(self):
        # / flat prices with small noise should classify as sideways
        n = 300
        np.random.seed(99)
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        closes = [100.0 + np.random.randn() * 0.5 for _ in range(n)]
        results = classify_regimes(dates, closes, "equity")
        classified = [r for r in results if r.regime != "insufficient_data"]
        assert len(classified) > 0
        sideways_count = sum(1 for r in classified if r.regime == "sideways")
        # / majority should be sideways
        assert sideways_count / len(classified) > 0.3

    def test_high_volatility_market_detected(self):
        # / calm period then extreme swings to trigger vol > 2x median
        n = 350
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        # / first 280 days: very calm, slight uptrend
        closes = [100.0 + i * 0.1 + np.sin(i * 0.1) * 0.5 for i in range(280)]
        # / last 70 days: extreme daily swings (+/- 5%)
        np.random.seed(42)
        for _i in range(70):
            closes.append(closes[-1] * (1 + np.random.choice([-0.05, 0.05])))
        results = classify_regimes(dates, closes, "equity")
        classified = [r for r in results if r.regime != "insufficient_data"]
        assert len(classified) > 0
        # / the volatile tail should trigger high_vol or bear+high_vol
        last_30 = classified[-30:]
        high_vol_count = sum(1 for r in last_30 if r.regime in ("high_vol", "bear"))
        assert high_vol_count > 0

    def test_regime_transitions_bull_to_bear(self):
        # / first half rising, second half falling sharply
        n = 400
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        # / bull phase: steady rise
        closes = [100.0 + i * 0.5 for i in range(300)]
        # / bear phase: sharp decline
        peak = closes[-1]
        for i in range(100):
            closes.append(peak * (1 - 0.30 * (i + 1) / 100))
        results = classify_regimes(dates, closes, "equity")
        classified = [r for r in results if r.regime != "insufficient_data"]
        # / should see both bull and bear/high_vol in the results
        regimes_seen = {r.regime for r in classified}
        assert "bull" in regimes_seen
        assert len(regimes_seen & {"bear", "high_vol"}) > 0


class TestClassifySingleDate:
    def test_insufficient_data(self):
        closes = [100.0] * 50
        result = classify_single_date(closes, date.today(), "equity")
        assert result.regime == "insufficient_data"
        assert result.confidence == 0.0

    def test_sufficient_data_returns_regime(self):
        closes = [100.0 + i * 0.3 for i in range(300)]
        result = classify_single_date(closes, date.today(), "equity")
        assert result.regime in ("bull", "bear", "sideways", "high_vol")
        assert result.confidence > 0

    def test_with_exactly_min_history_days_data_points(self):
        # / exactly MIN_HISTORY_DAYS should classify, not return insufficient_data
        closes = [100.0 + i * 0.3 for i in range(MIN_HISTORY_DAYS)]
        result = classify_single_date(closes, date.today(), "equity")
        assert result.regime in ("bull", "bear", "sideways", "high_vol")
        assert result.regime != "insufficient_data"
        assert result.confidence > 0


class TestBackfillRegimes:
    @pytest.mark.asyncio
    async def test_backfills_from_market_data(self):
        n = 250
        rows = [
            {"date": date(2020, 1, 1) + timedelta(days=i), "close": Decimal(str(100 + i * 0.5))}
            for i in range(n)
        ]

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = rows
        mock_conn.execute = AsyncMock()
        pool = _mock_pool(mock_conn)

        count = await backfill_regimes(pool, index_symbol="SPY", market="equity")
        assert count > 0
        assert mock_conn.execute.call_count > 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_data(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        count = await backfill_regimes(pool, index_symbol="SPY", market="equity")
        assert count == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_all_insufficient(self):
        # / only 50 rows — not enough for sma200
        rows = [
            {"date": date(2020, 1, 1) + timedelta(days=i), "close": Decimal("100")}
            for i in range(50)
        ]

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = rows
        pool = _mock_pool(mock_conn)

        count = await backfill_regimes(pool, index_symbol="SPY", market="equity")
        assert count == 0


class TestSnapshotRegimeDaily:
    # / phase 5 step 3: daily snapshots fill the gap between shift-triggered rows

    @pytest.mark.asyncio
    async def test_inserts_when_no_row_exists_today(self):
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = None  # / no existing row
        mock_conn.execute = AsyncMock()
        pool = _mock_pool(mock_conn)

        result = await snapshot_regime_daily(pool, "equity", "bull", confidence=0.75)

        assert result is True
        # / one SELECT (existence check) and one INSERT
        mock_conn.fetchval.assert_called_once()
        mock_conn.execute.assert_called_once()
        insert_sql = mock_conn.execute.call_args.args[0]
        assert "INSERT INTO regime_history" in insert_sql
        assert "is_snapshot" in insert_sql

    @pytest.mark.asyncio
    async def test_dedupes_when_row_already_exists_today(self):
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 1  # / row already present
        mock_conn.execute = AsyncMock()
        pool = _mock_pool(mock_conn)

        result = await snapshot_regime_daily(pool, "equity", "bull")

        assert result is False
        mock_conn.fetchval.assert_called_once()
        mock_conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_insufficient_data_regime(self):
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)

        result = await snapshot_regime_daily(pool, "equity", "insufficient_data")

        assert result is False
        mock_conn.fetchval.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_empty_inputs(self):
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)

        assert await snapshot_regime_daily(pool, "", "bull") is False
        assert await snapshot_regime_daily(pool, "equity", None) is False
        mock_conn.fetchval.assert_not_called()

    @pytest.mark.asyncio
    async def test_swallows_db_errors(self):
        mock_conn = AsyncMock()
        mock_conn.fetchval.side_effect = Exception("db down")
        pool = _mock_pool(mock_conn)

        # / must not raise; returns False so caller can move on
        result = await snapshot_regime_daily(pool, "equity", "bull")
        assert result is False

    @pytest.mark.asyncio
    async def test_confidence_rounded_to_3_decimals(self):
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = None
        mock_conn.execute = AsyncMock()
        pool = _mock_pool(mock_conn)

        await snapshot_regime_daily(pool, "crypto", "bear", confidence=0.123456789)

        # / arg index 4 is confidence in the insert call
        exec_args = mock_conn.execute.call_args.args
        conf_arg = exec_args[4]
        # / confidence is a Decimal rounded to 3 places
        assert str(conf_arg) == "0.123"

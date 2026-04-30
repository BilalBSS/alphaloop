# / tests for live strategy metrics writer (bug a)

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analysis.live_strategy_metrics import (
    MIN_TRADES,
    _compute_for_strategy,
    _compute_open_position_returns,
    _fifo_match_returns,
    compute_live_strategy_metrics,
)


def _mock_pool(mock_conn):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


def _trade(symbol: str, side: str, qty: float, price: float, days_ago: int = 0) -> dict:
    return {
        "symbol": symbol, "side": side,
        "qty": Decimal(str(qty)), "price": Decimal(str(price)),
        "pnl": None, "created_at": datetime(2026, 4, 10) - timedelta(days=days_ago),
    }


# ---------------------------------------------------------------------------
# / _fifo_match_returns
# ---------------------------------------------------------------------------

class TestFifoMatch:
    def test_single_buy_sell_pair(self):
        rows = [
            _trade("NVDA", "buy", 10, 100),
            _trade("NVDA", "sell", 10, 110),
        ]
        returns, pnl, closed = _fifo_match_returns(rows)
        assert len(returns) == 1
        assert returns[0] == pytest.approx(0.10, rel=1e-6)
        assert pnl == pytest.approx(100.0, rel=1e-6)
        assert len(closed) == 1

    def test_partial_sell_leaves_open_lot(self):
        rows = [
            _trade("NVDA", "buy", 10, 100),
            _trade("NVDA", "sell", 5, 110),
        ]
        returns, pnl, _closed = _fifo_match_returns(rows)
        assert len(returns) == 1
        assert pnl == pytest.approx(50.0, rel=1e-6)

    def test_multiple_buys_single_sell_fifo(self):
        # / buy 10 @ 100, buy 10 @ 120, sell 15 @ 110
        # / 10 shares from first buy (ret +10%, pnl +100)
        # / 5 shares from second buy (ret -8.33%, pnl -50)
        rows = [
            _trade("NVDA", "buy", 10, 100, days_ago=3),
            _trade("NVDA", "buy", 10, 120, days_ago=2),
            _trade("NVDA", "sell", 15, 110, days_ago=1),
        ]
        returns, pnl, _closed = _fifo_match_returns(rows)
        assert len(returns) == 2
        assert returns[0] == pytest.approx(0.10, rel=1e-6)
        assert returns[1] == pytest.approx(-10.0 / 120, rel=1e-6)
        assert pnl == pytest.approx(50.0, rel=1e-6)

    def test_cross_symbol_isolation(self):
        rows = [
            _trade("NVDA", "buy", 10, 100),
            _trade("META", "buy", 5, 200),
            _trade("NVDA", "sell", 10, 110),
            _trade("META", "sell", 5, 180),
        ]
        returns, pnl, _closed = _fifo_match_returns(rows)
        assert len(returns) == 2
        assert pnl == pytest.approx(100.0 - 100.0, rel=1e-6)  # / +100 nvda, -100 meta

    def test_unmatched_buy_ignored(self):
        # / only buys → no closed trades → empty returns
        rows = [_trade("NVDA", "buy", 10, 100)]
        returns, pnl, closed = _fifo_match_returns(rows)
        assert returns == []
        assert pnl == 0.0
        assert closed == []

    def test_sell_without_prior_buy_ignored(self):
        rows = [_trade("NVDA", "sell", 10, 100)]
        returns, pnl, _closed = _fifo_match_returns(rows)
        assert returns == []
        assert pnl == 0.0

    def test_zero_qty_skipped(self):
        rows = [
            _trade("NVDA", "buy", 10, 100),
            _trade("NVDA", "sell", 0, 110),
        ]
        returns, _pnl, _closed = _fifo_match_returns(rows)
        assert returns == []


# ---------------------------------------------------------------------------
# / _compute_for_strategy
# ---------------------------------------------------------------------------

class TestComputeForStrategy:
    @pytest.mark.asyncio
    async def test_returns_none_on_empty(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        result = await _compute_for_strategy(
            pool, "strategy_001", date(2026, 3, 1), date(2026, 4, 10),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_below_min_trades(self):
        # / only 2 closed trades — below MIN_TRADES threshold
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            _trade("NVDA", "buy", 10, 100, days_ago=5),
            _trade("NVDA", "sell", 10, 110, days_ago=4),
            _trade("META", "buy", 5, 500, days_ago=3),
            _trade("META", "sell", 5, 510, days_ago=2),
        ]
        pool = _mock_pool(mock_conn)

        # / bug e2: open position returns are zero in this test
        with patch("src.analysis.live_strategy_metrics._compute_open_position_returns",
                   new_callable=AsyncMock, return_value=([], 0.0)):
            result = await _compute_for_strategy(
                pool, "strategy_001", date(2026, 3, 1), date(2026, 4, 10),
            )
        assert result is None  # / 2 closed trades, need at least MIN_TRADES (3)

    @pytest.mark.asyncio
    async def test_computes_metrics_when_enough_trades(self):
        # / 3+ closed trade pairs
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            _trade("NVDA", "buy", 10, 100, days_ago=10),
            _trade("NVDA", "sell", 10, 110, days_ago=9),
            _trade("META", "buy", 5, 500, days_ago=8),
            _trade("META", "sell", 5, 510, days_ago=7),
            _trade("AAPL", "buy", 20, 150, days_ago=6),
            _trade("AAPL", "sell", 20, 155, days_ago=5),
        ]
        pool = _mock_pool(mock_conn)

        with patch("src.analysis.live_strategy_metrics._compute_open_position_returns",
                   new_callable=AsyncMock, return_value=([], 0.0)):
            result = await _compute_for_strategy(
                pool, "strategy_001", date(2026, 3, 1), date(2026, 4, 10),
            )
        assert result is not None
        assert result["total_trades"] == 3
        assert result["win_rate"] == 1.0  # / all three profitable
        assert result["sharpe_ratio"] > 0
        assert result["brier_score"] is None  # / not implemented

    @pytest.mark.asyncio
    async def test_losing_strategy_negative_sharpe(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            _trade("NVDA", "buy", 10, 100, days_ago=10),
            _trade("NVDA", "sell", 10, 95, days_ago=9),
            _trade("META", "buy", 5, 500, days_ago=8),
            _trade("META", "sell", 5, 480, days_ago=7),
            _trade("AAPL", "buy", 20, 150, days_ago=6),
            _trade("AAPL", "sell", 20, 145, days_ago=5),
        ]
        pool = _mock_pool(mock_conn)

        with patch("src.analysis.live_strategy_metrics._compute_open_position_returns",
                   new_callable=AsyncMock, return_value=([], 0.0)):
            result = await _compute_for_strategy(
                pool, "strategy_loser", date(2026, 3, 1), date(2026, 4, 10),
            )
        assert result is not None
        assert result["win_rate"] == 0.0
        assert result["sharpe_ratio"] < 0

    @pytest.mark.asyncio
    async def test_mixed_returns_win_rate(self):
        # / 2 wins, 1 loss -> 66.67% win rate
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            _trade("NVDA", "buy", 10, 100, days_ago=10),
            _trade("NVDA", "sell", 10, 110, days_ago=9),
            _trade("META", "buy", 5, 500, days_ago=8),
            _trade("META", "sell", 5, 480, days_ago=7),
            _trade("AAPL", "buy", 20, 150, days_ago=6),
            _trade("AAPL", "sell", 20, 155, days_ago=5),
        ]
        pool = _mock_pool(mock_conn)

        with patch("src.analysis.live_strategy_metrics._compute_open_position_returns",
                   new_callable=AsyncMock, return_value=([], 0.0)):
            result = await _compute_for_strategy(
                pool, "strategy_001", date(2026, 3, 1), date(2026, 4, 10),
            )
        assert result is not None
        assert result["win_rate"] == pytest.approx(2.0 / 3.0, rel=1e-3)

    @pytest.mark.asyncio
    async def test_open_positions_contribute_returns(self):
        # / bug e2: paper strategy with 0 closed trades but open positions should still
        # / produce metrics when daily mark-to-market observations satisfy MIN_TRADES
        mock_conn = AsyncMock()
        # / first fetch returns no trade_log rows (paper-only strategy)
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)
        # / 5 daily returns from open positions — above MIN_TRADES
        with patch("src.analysis.live_strategy_metrics._compute_open_position_returns",
                   new_callable=AsyncMock, return_value=([0.01, 0.02, -0.005, 0.015, 0.008], 125.0)):
            result = await _compute_for_strategy(
                pool, "strategy_paper", date(2026, 3, 1), date(2026, 4, 10),
            )
        assert result is not None
        assert result["total_trades"] == 0
        assert result["total_observations"] == 5
        assert result["sharpe_ratio"] > 0  # / mostly positive returns


# ---------------------------------------------------------------------------
# / compute_live_strategy_metrics (top-level)
# ---------------------------------------------------------------------------

class TestComputeLiveStrategyMetrics:
    @pytest.mark.asyncio
    async def test_no_strategies_returns_zero(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        result = await compute_live_strategy_metrics(pool)
        assert result == 0

    @pytest.mark.asyncio
    async def test_skips_untracked_and_null_strategies(self):
        # / only 'untracked' and NULL strategy_ids exist -> query returns 0 rows
        mock_conn = AsyncMock()
        # / first fetch: distinct strategy_ids (already filtered by WHERE clause)
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        result = await compute_live_strategy_metrics(pool)
        assert result == 0

    @pytest.mark.asyncio
    async def test_upserts_per_strategy_per_window(self):
        # / 1 strategy × 2 windows = 2 upserts
        mock_conn = AsyncMock()
        # / first call: list of distinct strategies
        # / subsequent calls: per-strategy trade rows (called twice, once per window)
        trade_rows = [
            _trade("NVDA", "buy", 10, 100, days_ago=10),
            _trade("NVDA", "sell", 10, 110, days_ago=9),
            _trade("META", "buy", 5, 500, days_ago=8),
            _trade("META", "sell", 5, 510, days_ago=7),
            _trade("AAPL", "buy", 20, 150, days_ago=6),
            _trade("AAPL", "sell", 20, 155, days_ago=5),
        ]
        mock_conn.fetch.side_effect = [
            [{"strategy_id": "strategy_011"}],  # / distinct strategies
            trade_rows,  # / 30-day window trades
            [],           # / 30-day brier join (empty)
            trade_rows,  # / 90-day window trades
            [],           # / 90-day brier join (empty)
        ]
        # / mock store_strategy_score (imported via tools)
        pool = _mock_pool(mock_conn)

        # / bug e2: mock open position returns so test doesn't try to fetch positions/market_data
        with patch("src.analysis.live_strategy_metrics._compute_open_position_returns",
                   new_callable=AsyncMock, return_value=([], 0.0)):
            with patch("src.analysis.live_strategy_metrics.store_strategy_score",
                       new_callable=AsyncMock, return_value=1) as mock_store:
                result = await compute_live_strategy_metrics(pool)

        assert result == 2  # / 1 strategy × 2 windows
        assert mock_store.call_count == 2

    @pytest.mark.asyncio
    async def test_custom_windows(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.side_effect = [
            [{"strategy_id": "s1"}],
            [],  # / 7-day window -> empty
            [],  # / 14-day window -> empty
        ]
        pool = _mock_pool(mock_conn)

        result = await compute_live_strategy_metrics(pool, windows_days=[7, 14])
        assert result == 0

    @pytest.mark.asyncio
    async def test_exception_does_not_crash_loop(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.side_effect = [
            [{"strategy_id": "s1"}, {"strategy_id": "s2"}],
            Exception("db boom"),  # / s1 30-day window fails
            Exception("db boom"),  # / s1 90-day window fails
            Exception("db boom"),  # / s2 30-day window fails
            Exception("db boom"),  # / s2 90-day window fails
        ]
        pool = _mock_pool(mock_conn)

        # / should not raise
        result = await compute_live_strategy_metrics(pool)
        assert result == 0


class TestMinTradesConstant:
    def test_min_trades_is_3(self):
        assert MIN_TRADES == 3


# ---------------------------------------------------------------------------
# / query window bounds (bug 4a: missed same-day trades)
# ---------------------------------------------------------------------------

class TestQueryWindowBounds:
    @pytest.mark.asyncio
    async def test_upper_bound_is_next_midnight(self):
        # / bug 4a root cause: WHERE created_at <= $3 missed trades that happened today
        # / fix: created_at < ($3::date + INTERVAL '1 day')
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        period_start = date(2026, 4, 1)
        period_end = date(2026, 4, 10)
        with patch("src.analysis.live_strategy_metrics._compute_open_position_returns",
                   new_callable=AsyncMock, return_value=([], 0.0)):
            await _compute_for_strategy(pool, "s1", period_start, period_end)

        # / bug e2: trade_log fetch is the first call; find it specifically
        trade_log_calls = [
            c for c in mock_conn.fetch.call_args_list
            if c.args and "FROM trade_log" in c.args[0]
        ]
        assert trade_log_calls, "expected trade_log fetch"
        call = trade_log_calls[0]
        sql = call.args[0]
        # / must use exclusive upper bound with +1 day shift, not inclusive <=
        assert "created_at < ($3::date + INTERVAL '1 day')" in sql
        assert "created_at <= $3" not in sql
        # / params: strategy_id, period_start, period_end
        assert call.args[1:] == ("s1", period_start, period_end)


class TestComputeOpenPositionReturnsBatched:
    @pytest.mark.asyncio
    async def test_no_per_position_acquire_loop(self):
        positions = [
            {"symbol": "NVDA", "qty": Decimal("10"), "avg_entry_price": Decimal("100"),
             "opened_at": datetime(2026, 4, 1)},
            {"symbol": "META", "qty": Decimal("5"), "avg_entry_price": Decimal("500"),
             "opened_at": datetime(2026, 4, 1)},
            {"symbol": "AAPL", "qty": Decimal("20"), "avg_entry_price": Decimal("150"),
             "opened_at": datetime(2026, 4, 1)},
        ]
        bars = [
            {"symbol": "NVDA", "date": date(2026, 4, 5), "close": Decimal("105")},
            {"symbol": "NVDA", "date": date(2026, 4, 6), "close": Decimal("110")},
            {"symbol": "META", "date": date(2026, 4, 5), "close": Decimal("510")},
            {"symbol": "AAPL", "date": date(2026, 4, 5), "close": Decimal("155")},
        ]
        mock_conn = AsyncMock()
        # / first fetch: positions; second fetch: batched bars
        mock_conn.fetch.side_effect = [positions, bars]
        pool = _mock_pool(mock_conn)

        returns, unrealized = await _compute_open_position_returns(
            pool, "strategy_001", date(2026, 4, 1), date(2026, 4, 10),
        )

        assert mock_conn.fetch.call_count == 2
        assert pool.acquire.call_count == 1
        bars_sql = mock_conn.fetch.call_args_list[1].args[0]
        assert "ANY($1" in bars_sql
        assert "FROM market_data" in bars_sql
        symbols_arg = mock_conn.fetch.call_args_list[1].args[1]
        assert sorted(symbols_arg) == ["AAPL", "META", "NVDA"]
        assert len(returns) > 0
        assert isinstance(unrealized, float)

    @pytest.mark.asyncio
    async def test_math_unchanged_after_batching(self):
        positions = [
            {"symbol": "NVDA", "qty": Decimal("10"), "avg_entry_price": Decimal("100"),
             "opened_at": datetime(2026, 4, 1)},
            {"symbol": "META", "qty": Decimal("5"), "avg_entry_price": Decimal("500"),
             "opened_at": datetime(2026, 4, 1)},
            {"symbol": "AAPL", "qty": Decimal("20"), "avg_entry_price": Decimal("150"),
             "opened_at": datetime(2026, 4, 1)},
        ]
        bars = [
            # / sorted by symbol, date as the SQL ORDER BY guarantees
            {"symbol": "AAPL", "date": date(2026, 4, 5), "close": Decimal("155")},
            {"symbol": "META", "date": date(2026, 4, 5), "close": Decimal("510")},
            {"symbol": "NVDA", "date": date(2026, 4, 5), "close": Decimal("105")},
            {"symbol": "NVDA", "date": date(2026, 4, 6), "close": Decimal("110")},
        ]
        mock_conn = AsyncMock()
        mock_conn.fetch.side_effect = [positions, bars]
        pool = _mock_pool(mock_conn)

        returns, unrealized = await _compute_open_position_returns(
            pool, "strategy_001", date(2026, 4, 1), date(2026, 4, 10),
        )

        # / NVDA: (105-100)/100 = 0.05 then (110-105)/105 = 0.04762
        # / META: (510-500)/500 = 0.02
        # / AAPL: (155-150)/150 = 0.03333
        assert len(returns) == 4
        assert sorted(returns) == pytest.approx(
            sorted([0.05, 0.04761904, 0.02, 0.03333333]), rel=1e-4
        )
        assert unrealized == pytest.approx(250.0, rel=1e-6)

    @pytest.mark.asyncio
    async def test_no_positions_returns_empty(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        returns, unrealized = await _compute_open_position_returns(
            pool, "strategy_001", date(2026, 4, 1), date(2026, 4, 10),
        )
        assert returns == []
        assert unrealized == 0.0
        # / single fetch (positions only); no second batched query when nothing to fetch
        assert mock_conn.fetch.call_count == 1

    @pytest.mark.asyncio
    async def test_skips_zero_qty_and_invalid_entry(self):
        # / positions with qty <= 0 or entry <= 0 are filtered out; symbols arg only
        # / contains valid ones.
        positions = [
            {"symbol": "NVDA", "qty": Decimal("10"), "avg_entry_price": Decimal("100"),
             "opened_at": datetime(2026, 4, 1)},
            {"symbol": "BAD1", "qty": Decimal("0"), "avg_entry_price": Decimal("50"),
             "opened_at": datetime(2026, 4, 1)},
            {"symbol": "BAD2", "qty": Decimal("5"), "avg_entry_price": Decimal("0"),
             "opened_at": datetime(2026, 4, 1)},
        ]
        mock_conn = AsyncMock()
        mock_conn.fetch.side_effect = [
            positions,
            [{"symbol": "NVDA", "date": date(2026, 4, 5), "close": Decimal("110")}],
        ]
        pool = _mock_pool(mock_conn)

        await _compute_open_position_returns(
            pool, "strategy_001", date(2026, 4, 1), date(2026, 4, 10),
        )
        # / batched query only includes NVDA, not BAD1/BAD2
        symbols_arg = mock_conn.fetch.call_args_list[1].args[1]
        assert symbols_arg == ["NVDA"]

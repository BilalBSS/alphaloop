# / tests for agent pipeline db helpers

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.data_tools import (
    dict_to_analysis_data,
    fetch_analysis_score,
    store_analysis_score,
)
from src.agents.position_tools import (
    close_strategy_position,
    fetch_most_recent_open_entry,
    get_strategy_positions,
    open_strategy_position,
    reconcile_strategy_positions,
    sync_strategy_positions_from_alpaca,
)
from src.agents.sync_tools import backfill_trade_pnl, sync_trades_from_alpaca
from src.agents.trade_tools import (
    _STATUS_TABLES,
    fetch_pending_signals,
    fetch_pending_trades,
    store_approved_trade,
    store_trade_log,
    store_trade_signal,
    update_trade_status,
)
from src.data.strategy_metrics import (
    fetch_strategy_scores,
    store_evolution_log,
    store_strategy_score,
)
from src.data.synthesis import fetch_daily_synthesis, store_daily_synthesis
from src.data.trade_history import fetch_recent_trades


def _mock_pool(mock_conn):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


# -- store_analysis_score --

class TestStoreAnalysisScore:
    @pytest.mark.asyncio
    async def test_returns_id(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 42}
        pool = _mock_pool(mock_conn)

        result = await store_analysis_score(
            pool, "AAPL", date(2026, 3, 25), 85.0, 72.0, 78.5,
            "bull", 0.85, True, {"notes": "strong"},
        )
        assert result == 42

    @pytest.mark.asyncio
    async def test_passes_correct_args(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 1}
        pool = _mock_pool(mock_conn)

        await store_analysis_score(
            pool, "MSFT", date(2026, 1, 1), 90.0, None, 90.0,
            "sideways", 0.6, True,
        )
        args = mock_conn.fetchrow.call_args[0]
        # / positional: sql, symbol, date, fund, tech, comp, regime, conf, used_fund, details
        assert args[1] == "MSFT"
        assert args[2] == date(2026, 1, 1)
        assert args[3] == Decimal("90.0")
        assert args[4] is None  # / technical_score was None
        assert args[5] == Decimal("90.0")
        assert args[6] == "sideways"
        assert args[7] == Decimal("0.6")
        assert args[8] is True

    @pytest.mark.asyncio
    async def test_decimal_conversion(self):
        # / floats become Decimal for db storage
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 1}
        pool = _mock_pool(mock_conn)

        await store_analysis_score(
            pool, "GOOG", date(2026, 1, 1), 55.5, 60.3, 57.9,
            "bear", 0.72, False,
        )
        args = mock_conn.fetchrow.call_args[0]
        assert isinstance(args[3], Decimal)
        assert isinstance(args[4], Decimal)
        assert isinstance(args[5], Decimal)
        assert isinstance(args[7], Decimal)

    @pytest.mark.asyncio
    async def test_passes_details_dict(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 1}
        pool = _mock_pool(mock_conn)

        details = {"ratio_scores": [1, 2, 3]}
        await store_analysis_score(
            pool, "TSLA", date(2026, 1, 1), 50.0, 50.0, 50.0,
            None, None, True, details,
        )
        args = mock_conn.fetchrow.call_args[0]
        # / raw dict passed to asyncpg, codec handles serialization
        assert args[9] == details

    @pytest.mark.asyncio
    async def test_none_details_passes_none(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 1}
        pool = _mock_pool(mock_conn)

        await store_analysis_score(
            pool, "TSLA", date(2026, 1, 1), 50.0, 50.0, 50.0,
            None, None, True, None,
        )
        args = mock_conn.fetchrow.call_args[0]
        assert args[9] is None

    @pytest.mark.asyncio
    async def test_sql_contains_upsert(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 1}
        pool = _mock_pool(mock_conn)

        await store_analysis_score(
            pool, "X", date(2026, 1, 1), 1.0, 1.0, 1.0,
            None, None, False,
        )
        sql = mock_conn.fetchrow.call_args[0][0]
        assert "ON CONFLICT" in sql
        assert "RETURNING id" in sql


# -- fetch_analysis_score --

class TestFetchAnalysisScore:
    @pytest.mark.asyncio
    async def test_returns_dict(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 1, "symbol": "AAPL", "composite_score": 80}
        pool = _mock_pool(mock_conn)

        result = await fetch_analysis_score(pool, "AAPL", date(2026, 3, 25))
        assert result == {"id": 1, "symbol": "AAPL", "composite_score": 80}

    @pytest.mark.asyncio
    async def test_returns_none_when_no_row(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        pool = _mock_pool(mock_conn)

        result = await fetch_analysis_score(pool, "FAKE")
        assert result is None

    @pytest.mark.asyncio
    async def test_passes_symbol_and_date(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        pool = _mock_pool(mock_conn)

        await fetch_analysis_score(pool, "NVDA", date(2026, 6, 1))
        args = mock_conn.fetchrow.call_args[0]
        assert args[1] == "NVDA"
        assert args[2] == date(2026, 6, 1)


# -- store_trade_signal --

class TestStoreTradeSignal:
    @pytest.mark.asyncio
    async def test_returns_id(self):
        mock_conn = AsyncMock()
        # / first fetchrow = SELECT existing (None), second = INSERT returning id
        mock_conn.fetchrow.side_effect = [None, {"id": 7}]
        pool = _mock_pool(mock_conn)

        result = await store_trade_signal(
            pool, "strategy_001", "AAPL", "buy", 0.85, "bull",
        )
        assert result == 7

    @pytest.mark.asyncio
    async def test_strength_as_decimal(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [None, {"id": 1}]
        pool = _mock_pool(mock_conn)

        await store_trade_signal(pool, "s1", "MSFT", "sell", 0.42, None)
        # / second fetchrow call is the INSERT
        args = mock_conn.fetchrow.call_args_list[1][0]
        assert args[4] == Decimal("0.42")

    @pytest.mark.asyncio
    async def test_details_json_dumped(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [None, {"id": 1}]
        pool = _mock_pool(mock_conn)

        details = {"reason": "oversold"}
        await store_trade_signal(pool, "s1", "X", "buy", 0.5, None, details)
        args = mock_conn.fetchrow.call_args_list[1][0]
        assert args[6] == details

    @pytest.mark.asyncio
    async def test_sql_has_pending_status(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [None, {"id": 1}]
        pool = _mock_pool(mock_conn)

        await store_trade_signal(pool, "s1", "X", "buy", 0.5, None)
        sql = mock_conn.fetchrow.call_args[0][0]
        assert "'pending'" in sql

    @pytest.mark.asyncio
    async def test_dedup_skips_rejected(self):
        # / rejected signal today — should return existing id without insert or update
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 99, "status": "rejected"}
        pool = _mock_pool(mock_conn)

        result = await store_trade_signal(pool, "s1", "AAPL", "buy", 0.9, "bull")
        assert result == 99
        # / only one fetchrow call (the SELECT), no INSERT
        assert mock_conn.fetchrow.call_count == 1
        mock_conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_dedup_updates_pending(self):
        # / pending signal today — should update strength, not insert new row
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 50, "status": "pending"}
        pool = _mock_pool(mock_conn)

        result = await store_trade_signal(pool, "s1", "AAPL", "buy", 0.75, "bear")
        assert result == 50
        # / only one fetchrow (SELECT), no second fetchrow (INSERT)
        assert mock_conn.fetchrow.call_count == 1
        # / execute called once for the UPDATE
        mock_conn.execute.assert_called_once()
        update_args = mock_conn.execute.call_args[0]
        assert update_args[1] == Decimal("0.75")
        assert update_args[2] == "bear"
        assert update_args[4] == 50

    @pytest.mark.asyncio
    async def test_dedup_skips_processed(self):
        # / processed signal today — should return existing id without insert or update
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 77, "status": "processed"}
        pool = _mock_pool(mock_conn)

        result = await store_trade_signal(pool, "s1", "TSLA", "sell", 0.6, None)
        assert result == 77
        assert mock_conn.fetchrow.call_count == 1
        mock_conn.execute.assert_not_called()


# -- fetch_pending_signals --

class TestFetchPendingSignals:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {"id": 1, "symbol": "AAPL"},
            {"id": 2, "symbol": "MSFT"},
        ]
        pool = _mock_pool(mock_conn)

        result = await fetch_pending_signals(pool)
        assert len(result) == 2
        assert result[0] == {"id": 1, "symbol": "AAPL"}

    @pytest.mark.asyncio
    async def test_returns_empty_list(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        result = await fetch_pending_signals(pool)
        assert result == []

    @pytest.mark.asyncio
    async def test_passes_limit(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        await fetch_pending_signals(pool, limit=10)
        args = mock_conn.fetch.call_args[0]
        assert args[1] == 10


# -- store_approved_trade --

class TestStoreApprovedTrade:
    @pytest.mark.asyncio
    async def test_returns_id(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 99}
        pool = _mock_pool(mock_conn)

        result = await store_approved_trade(pool, 7, "AAPL", "buy", 10.0)
        assert result == 99

    @pytest.mark.asyncio
    async def test_qty_as_decimal(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 1}
        pool = _mock_pool(mock_conn)

        await store_approved_trade(pool, 1, "MSFT", "sell", 5.5, "limit", "s1")
        args = mock_conn.fetchrow.call_args[0]
        assert args[4] == Decimal("5.5")

    @pytest.mark.asyncio
    async def test_default_order_type(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 1}
        pool = _mock_pool(mock_conn)

        await store_approved_trade(pool, 1, "X", "buy", 1.0)
        args = mock_conn.fetchrow.call_args[0]
        assert args[5] == "market"


# -- fetch_pending_trades --

class TestFetchPendingTrades:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [{"id": 1, "symbol": "GOOG"}]
        pool = _mock_pool(mock_conn)

        result = await fetch_pending_trades(pool)
        assert result == [{"id": 1, "symbol": "GOOG"}]

    @pytest.mark.asyncio
    async def test_empty_result(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        result = await fetch_pending_trades(pool)
        assert result == []


# -- update_trade_status --

class TestUpdateTradeStatus:
    @pytest.mark.asyncio
    async def test_valid_table_trade_signals(self):
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "UPDATE 1"
        pool = _mock_pool(mock_conn)

        result = await update_trade_status(pool, "trade_signals", 5, "approved")
        assert result is True

    @pytest.mark.asyncio
    async def test_valid_table_approved_trades(self):
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "UPDATE 1"
        pool = _mock_pool(mock_conn)

        result = await update_trade_status(pool, "approved_trades", 3, "executed")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_row_updated(self):
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "UPDATE 0"
        pool = _mock_pool(mock_conn)

        result = await update_trade_status(pool, "trade_signals", 999, "rejected")
        assert result is False

    @pytest.mark.asyncio
    async def test_rejects_invalid_table(self):
        pool = MagicMock()
        with pytest.raises(ValueError, match="invalid table"):
            await update_trade_status(pool, "users; DROP TABLE trade_signals;--", 1, "x")

    @pytest.mark.asyncio
    async def test_rejects_trade_log_table(self):
        pool = MagicMock()
        with pytest.raises(ValueError):
            await update_trade_status(pool, "trade_log", 1, "x")

    def test_status_tables_whitelist(self):
        assert {"trade_signals", "approved_trades"} == _STATUS_TABLES


# -- store_trade_log --

class TestStoreTradeLog:
    @pytest.mark.asyncio
    async def test_returns_id(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 200}
        pool = _mock_pool(mock_conn)

        result = await store_trade_log(
            pool, 99, "AAPL", "buy", 10.0, 150.50, "ord_123", "alpaca", "bull", 25.0,
        )
        assert result == 200

    @pytest.mark.asyncio
    async def test_decimal_conversions(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 1}
        pool = _mock_pool(mock_conn)

        await store_trade_log(
            pool, 1, "MSFT", "sell", 5.0, 300.25, None, None, None, -10.5,
        )
        args = mock_conn.fetchrow.call_args[0]
        assert args[4] == Decimal("5.0")
        assert args[5] == Decimal("300.25")
        assert args[9] == Decimal("-10.5")

    @pytest.mark.asyncio
    async def test_none_pnl_stays_none(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 1}
        pool = _mock_pool(mock_conn)

        await store_trade_log(
            pool, None, "X", "buy", 1.0, 10.0, None, None, None, None,
        )
        args = mock_conn.fetchrow.call_args[0]
        assert args[9] is None

    @pytest.mark.asyncio
    async def test_details_json_dumped(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 1}
        pool = _mock_pool(mock_conn)

        details = {"slippage": 0.01}
        await store_trade_log(
            pool, 1, "X", "buy", 1.0, 10.0, None, None, None, None,
            details=details,
        )
        args = mock_conn.fetchrow.call_args[0]
        assert args[11] == details


# -- store_strategy_score --

class TestStoreStrategyScore:
    @pytest.mark.asyncio
    async def test_returns_id(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 5}
        pool = _mock_pool(mock_conn)

        result = await store_strategy_score(
            pool, "strat_001", date(2026, 1, 1), date(2026, 3, 1),
            1.42, -0.12, 0.58, 0.18, 50,
        )
        assert result == 5

    @pytest.mark.asyncio
    async def test_decimal_conversions(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 1}
        pool = _mock_pool(mock_conn)

        await store_strategy_score(
            pool, "s1", date(2026, 1, 1), date(2026, 3, 1),
            1.5, -0.08, 0.65, 0.15, 100,
        )
        args = mock_conn.fetchrow.call_args[0]
        assert args[4] == Decimal("1.5")
        assert args[5] == Decimal("-0.08")
        assert args[6] == Decimal("0.65")
        assert args[7] == Decimal("0.15")

    @pytest.mark.asyncio
    async def test_none_brier_stays_none(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 1}
        pool = _mock_pool(mock_conn)

        await store_strategy_score(
            pool, "s1", date(2026, 1, 1), date(2026, 3, 1),
            1.0, -0.05, 0.50, None, 10,
        )
        args = mock_conn.fetchrow.call_args[0]
        assert args[7] is None

    @pytest.mark.asyncio
    async def test_regime_breakdown_json(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 1}
        pool = _mock_pool(mock_conn)

        breakdown = {"bull": 1.5, "bear": -0.3}
        await store_strategy_score(
            pool, "s1", date(2026, 1, 1), date(2026, 3, 1),
            1.0, -0.05, 0.50, None, 10, breakdown,
        )
        args = mock_conn.fetchrow.call_args[0]
        assert args[9] == breakdown


# -- fetch_strategy_scores --

class TestFetchStrategyScores:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {"id": 1, "strategy_id": "s1"},
            {"id": 2, "strategy_id": "s2"},
        ]
        pool = _mock_pool(mock_conn)

        result = await fetch_strategy_scores(pool)
        assert len(result) == 2
        assert result[0]["strategy_id"] == "s1"

    @pytest.mark.asyncio
    async def test_empty_result(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        result = await fetch_strategy_scores(pool)
        assert result == []


# -- store_evolution_log --

class TestStoreEvolutionLog:
    @pytest.mark.asyncio
    async def test_returns_id(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 10}
        pool = _mock_pool(mock_conn)

        result = await store_evolution_log(
            pool, 3, "kill", "strat_005", None, "underperforming",
        )
        assert result == 10

    @pytest.mark.asyncio
    async def test_details_json_dumped(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 1}
        pool = _mock_pool(mock_conn)

        details = {"sharpe": 0.3}
        await store_evolution_log(
            pool, 1, "mutate", "s2", "s1", "low sharpe", details,
        )
        args = mock_conn.fetchrow.call_args[0]
        assert args[6] == details


# -- fetch_recent_trades --

class TestFetchRecentTrades:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [{"id": 1}]
        pool = _mock_pool(mock_conn)

        result = await fetch_recent_trades(pool)
        assert result == [{"id": 1}]

    @pytest.mark.asyncio
    async def test_filters_by_strategy(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        await fetch_recent_trades(pool, strategy_id="s1", limit=25)
        args = mock_conn.fetch.call_args[0]
        assert "strategy_id" in args[0]
        assert args[1] == "s1"
        assert args[2] == 25

    @pytest.mark.asyncio
    async def test_no_strategy_filter(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        await fetch_recent_trades(pool, limit=10)
        args = mock_conn.fetch.call_args[0]
        assert "strategy_id" not in args[0]
        assert args[1] == 10

    @pytest.mark.asyncio
    async def test_empty_result(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        result = await fetch_recent_trades(pool, strategy_id="nonexistent")
        assert result == []


# -- dict_to_analysis_data --

class TestDictToAnalysisData:
    def test_consecutive_beats_default(self):
        # / missing key defaults to 0
        d = {"pe_ratio": 10.0}
        result = dict_to_analysis_data(d)
        assert result.consecutive_beats == 0

    def test_extra_keys_ignored(self):
        # / dict_to_analysis_data should not crash on extra keys
        d = {"pe_ratio": 10.0, "unknown_field": "ignored"}
        result = dict_to_analysis_data(d)
        assert result.pe_ratio == 10.0


# -- store_daily_synthesis / fetch_daily_synthesis --

class TestDailySynthesis:
    @pytest.mark.asyncio
    async def test_store_returns_id(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 5}
        pool = _mock_pool(mock_conn)

        result = await store_daily_synthesis(
            pool, date(2026, 3, 27), "deepseek-reasoner",
            [{"symbol": "NVDA"}], [{"symbol": "MRNA"}],
            "moderate risk", {"AAPL": "watching"}, "raw text",
        )
        assert result == 5

    @pytest.mark.asyncio
    async def test_store_passes_json_args(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 1}
        pool = _mock_pool(mock_conn)

        buys = [{"symbol": "CRM", "score": 53.0}]
        avoids = [{"symbol": "TSLA", "score": 3.3}]
        notes = {"NVDA": "strong momentum"}

        await store_daily_synthesis(
            pool, date(2026, 3, 27), "deepseek-reasoner",
            buys, avoids, "low risk", notes, "raw",
        )
        args = mock_conn.fetchrow.call_args[0]
        assert args[1] == date(2026, 3, 27)
        assert args[2] == "deepseek-reasoner"
        # / top_buys and top_avoids are json-serialized
        # / raw lists passed to asyncpg, codec handles serialization
        assert args[3] == buys
        assert args[4] == avoids

    @pytest.mark.asyncio
    async def test_fetch_latest(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "id": 1, "date": date(2026, 3, 27),
            "model": "deepseek-reasoner", "top_buys": "[]",
        }
        pool = _mock_pool(mock_conn)

        result = await fetch_daily_synthesis(pool)
        assert result is not None
        assert result["model"] == "deepseek-reasoner"

    @pytest.mark.asyncio
    async def test_fetch_by_date(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": 2, "date": date(2026, 3, 26)}
        pool = _mock_pool(mock_conn)

        result = await fetch_daily_synthesis(pool, target_date=date(2026, 3, 26))
        assert result["date"] == date(2026, 3, 26)
        args = mock_conn.fetchrow.call_args[0]
        assert args[1] == date(2026, 3, 26)

    @pytest.mark.asyncio
    async def test_fetch_returns_none_when_empty(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        pool = _mock_pool(mock_conn)

        result = await fetch_daily_synthesis(pool)
        assert result is None


# -- open_strategy_position --

class TestOpenStrategyPosition:
    @pytest.mark.asyncio
    async def test_inserts_new_position(self):
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)

        await open_strategy_position(pool, "strat_001", "AAPL", 10.0, 150.0)
        args = mock_conn.execute.call_args[0]
        assert "INSERT INTO strategy_positions" in args[0]
        assert args[1] == "strat_001"
        assert args[2] == "AAPL"
        assert args[3] == Decimal("10.0")
        assert args[4] == Decimal("150.0")

    @pytest.mark.asyncio
    async def test_upsert_sql_averages_entry_price(self):
        # / ON CONFLICT recalculates weighted avg entry
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)

        await open_strategy_position(pool, "strat_001", "MSFT", 5.0, 300.0)
        sql = mock_conn.execute.call_args[0][0]
        assert "ON CONFLICT" in sql
        assert "avg_entry_price" in sql
        assert "NULLIF" in sql

    @pytest.mark.asyncio
    async def test_zero_qty(self):
        # / zero qty still executes — db NULLIF prevents division by zero
        mock_conn = AsyncMock()
        pool = _mock_pool(mock_conn)

        await open_strategy_position(pool, "strat_001", "GOOG", 0.0, 100.0)
        args = mock_conn.execute.call_args[0]
        assert args[3] == Decimal("0.0")


# -- close_strategy_position --

def _mock_pool_with_transaction(mock_conn):
    # / close_strategy_position uses conn.transaction() inside pool.acquire()
    # / transaction() is a sync call returning an async context manager
    mock_txn = MagicMock()
    mock_txn.__aenter__ = AsyncMock(return_value=None)
    mock_txn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_txn)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


class TestCloseStrategyPosition:
    @pytest.mark.asyncio
    async def test_full_close_deletes_row(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"qty": Decimal("10"), "avg_entry_price": Decimal("150.50")}
        pool = _mock_pool_with_transaction(mock_conn)

        result = await close_strategy_position(pool, "strat_001", "AAPL", 10.0)
        assert result == 150.50
        # / should DELETE, not UPDATE
        delete_sql = mock_conn.execute.call_args[0][0]
        assert "DELETE" in delete_sql

    @pytest.mark.asyncio
    async def test_partial_close_updates_qty(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"qty": Decimal("10"), "avg_entry_price": Decimal("200.0")}
        pool = _mock_pool_with_transaction(mock_conn)

        result = await close_strategy_position(pool, "strat_001", "MSFT", 3.0)
        assert result == 200.0
        # / should UPDATE with remaining = 7.0
        update_call = mock_conn.execute.call_args[0]
        assert "UPDATE" in update_call[0]
        assert update_call[1] == Decimal("7.0")

    @pytest.mark.asyncio
    async def test_returns_none_when_no_position(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        pool = _mock_pool_with_transaction(mock_conn)

        result = await close_strategy_position(pool, "strat_001", "FAKE", 5.0)
        assert result is None
        # / no execute call after fetchrow returned None
        mock_conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_overclose_deletes_row(self):
        # / closing more than held qty still deletes
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"qty": Decimal("5"), "avg_entry_price": Decimal("100.0")}
        pool = _mock_pool_with_transaction(mock_conn)

        result = await close_strategy_position(pool, "strat_001", "TSLA", 20.0)
        assert result == 100.0
        delete_sql = mock_conn.execute.call_args[0][0]
        assert "DELETE" in delete_sql


# -- get_strategy_positions --

class TestGetStrategyPositions:
    @pytest.mark.asyncio
    async def test_returns_positions_for_strategy(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {"strategy_id": "s1", "symbol": "AAPL", "qty": Decimal("10"), "avg_entry_price": Decimal("150.0"), "updated_at": None},
            {"strategy_id": "s1", "symbol": "MSFT", "qty": Decimal("5"), "avg_entry_price": Decimal("300.0"), "updated_at": None},
        ]
        pool = _mock_pool(mock_conn)

        result = await get_strategy_positions(pool, strategy_id="s1")
        assert len(result) == 2
        assert result[0]["symbol"] == "AAPL"
        assert result[0]["qty"] == 10.0
        assert result[1]["avg_entry_price"] == 300.0

    @pytest.mark.asyncio
    async def test_empty_result(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        result = await get_strategy_positions(pool, strategy_id="nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_by_symbol(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {"strategy_id": "s1", "symbol": "AAPL", "qty": Decimal("10"), "avg_entry_price": Decimal("150.0"), "updated_at": None},
        ]
        pool = _mock_pool(mock_conn)

        await get_strategy_positions(pool, strategy_id="s1", symbol="AAPL")
        sql = mock_conn.fetch.call_args[0][0]
        assert "strategy_id" in sql
        assert "symbol" in sql
        args = mock_conn.fetch.call_args[0]
        assert args[1] == "s1"
        assert args[2] == "AAPL"

    @pytest.mark.asyncio
    async def test_symbol_only_filter(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        await get_strategy_positions(pool, symbol="NVDA")
        sql = mock_conn.fetch.call_args[0][0]
        assert "symbol" in sql
        # / should not filter by strategy_id
        assert "strategy_id = $" not in sql

    @pytest.mark.asyncio
    async def test_no_filters_returns_all(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        await get_strategy_positions(pool)
        sql = mock_conn.fetch.call_args[0][0]
        assert "WHERE" not in sql

    @pytest.mark.asyncio
    async def test_none_avg_entry_price(self):
        # / avg_entry_price can be None (e.g. after zero-qty division)
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {"strategy_id": "s1", "symbol": "X", "qty": Decimal("1"), "avg_entry_price": None, "updated_at": None},
        ]
        pool = _mock_pool(mock_conn)

        result = await get_strategy_positions(pool, strategy_id="s1")
        assert result[0]["avg_entry_price"] is None


# -- sync_trades_from_alpaca --

class TestSyncTradesFromAlpaca:
    @pytest.mark.asyncio
    @patch("src.data.alpaca_client.get_alpaca_client")
    @patch("src.data.alpaca_client.alpaca_headers")
    @patch("src.data.alpaca_client.alpaca_base_url")
    async def test_syncs_new_trades(self, mock_base_url, mock_headers, mock_get_client):
        mock_base_url.return_value = "https://paper-api.alpaca.markets"
        mock_headers.return_value = {"APCA-API-KEY-ID": "test"}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {"id": "ord_1", "symbol": "AAPL", "side": "buy", "filled_qty": "10",
             "filled_avg_price": "150.0", "filled_at": "2026-04-01T10:00:00Z",
             "type": "market", "time_in_force": "day"},
        ]
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        # / first acquire: lookup existing order_ids (none exist)
        # / second acquire: insert new trades
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []  # / no existing orders
        # / bug 1a: sync checks if a real strategy owns the symbol before projecting
        # / fetchrow returns None = not owned, safe to project to untracked
        mock_conn.fetchrow.return_value = None

        # / need two acquire calls — pool returns same conn for both
        pool = _mock_pool(mock_conn)

        result = await sync_trades_from_alpaca(pool)
        assert result == 1
        # / bug 1a: sync projects fills into strategy_positions when no real owner
        # / buy path: 1 trade_log insert + 1 strategy_positions upsert = 2 execute calls
        assert mock_conn.execute.call_count == 2

    @pytest.mark.asyncio
    @patch("src.data.alpaca_client.get_alpaca_client")
    @patch("src.data.alpaca_client.alpaca_headers")
    @patch("src.data.alpaca_client.alpaca_base_url")
    async def test_skips_existing_trades(self, mock_base_url, mock_headers, mock_get_client):
        mock_base_url.return_value = "https://paper-api.alpaca.markets"
        mock_headers.return_value = {}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {"id": "ord_1", "symbol": "AAPL", "side": "buy", "filled_qty": "10",
             "filled_avg_price": "150.0", "filled_at": "2026-04-01T10:00:00Z",
             "type": "market", "time_in_force": "day"},
        ]
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        mock_conn = AsyncMock()
        # / ord_1 already in trade_log
        mock_conn.fetch.return_value = [{"order_id": "ord_1"}]
        pool = _mock_pool(mock_conn)

        result = await sync_trades_from_alpaca(pool)
        assert result == 0
        mock_conn.execute.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.data.alpaca_client.get_alpaca_client")
    @patch("src.data.alpaca_client.alpaca_headers")
    @patch("src.data.alpaca_client.alpaca_base_url")
    async def test_handles_empty_orders(self, mock_base_url, mock_headers, mock_get_client):
        mock_base_url.return_value = "https://paper-api.alpaca.markets"
        mock_headers.return_value = {}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = []
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        result = await sync_trades_from_alpaca(pool)
        assert result == 0

    @pytest.mark.asyncio
    @patch("src.data.alpaca_client.get_alpaca_client")
    @patch("src.data.alpaca_client.alpaca_headers")
    @patch("src.data.alpaca_client.alpaca_base_url")
    async def test_api_failure_returns_zero(self, mock_base_url, mock_headers, mock_get_client):
        mock_base_url.return_value = "https://paper-api.alpaca.markets"
        mock_headers.return_value = {}
        mock_get_client.side_effect = Exception("connection refused")

        pool = MagicMock()
        result = await sync_trades_from_alpaca(pool)
        assert result == 0

    @pytest.mark.asyncio
    @patch("src.data.alpaca_client.get_alpaca_client")
    @patch("src.data.alpaca_client.alpaca_headers")
    @patch("src.data.alpaca_client.alpaca_base_url")
    async def test_skips_zero_qty_orders(self, mock_base_url, mock_headers, mock_get_client):
        mock_base_url.return_value = "https://paper-api.alpaca.markets"
        mock_headers.return_value = {}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {"id": "ord_1", "symbol": "AAPL", "side": "buy", "filled_qty": "0",
             "filled_avg_price": "150.0", "filled_at": None,
             "type": "market", "time_in_force": "day"},
        ]
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        result = await sync_trades_from_alpaca(pool)
        assert result == 0
        mock_conn.execute.assert_not_called()


# -- sync_strategy_positions_from_alpaca --

class TestSyncStrategyPositionsFromAlpaca:
    @pytest.mark.asyncio
    @patch("src.agents.position_tools.open_strategy_position")
    @patch("src.data.alpaca_client.get_alpaca_client")
    @patch("src.data.alpaca_client.alpaca_headers")
    @patch("src.data.alpaca_client.alpaca_base_url")
    async def test_bootstraps_untracked_positions(self, mock_base_url, mock_headers, mock_get_client, mock_open):
        mock_base_url.return_value = "https://paper-api.alpaca.markets"
        mock_headers.return_value = {}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {"symbol": "AAPL", "qty": "10", "avg_entry_price": "150.0"},
        ]
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        mock_conn = AsyncMock()
        # / no tracked positions in db
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        result = await sync_strategy_positions_from_alpaca(pool)
        assert result == 1
        mock_open.assert_called_once_with(pool, "untracked", "AAPL", 10.0, 150.0)

    @pytest.mark.asyncio
    @patch("src.agents.position_tools.open_strategy_position")
    @patch("src.data.alpaca_client.get_alpaca_client")
    @patch("src.data.alpaca_client.alpaca_headers")
    @patch("src.data.alpaca_client.alpaca_base_url")
    async def test_skips_already_tracked(self, mock_base_url, mock_headers, mock_get_client, mock_open):
        mock_base_url.return_value = "https://paper-api.alpaca.markets"
        mock_headers.return_value = {}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {"symbol": "AAPL", "qty": "10", "avg_entry_price": "150.0"},
        ]
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        mock_conn = AsyncMock()
        # / db already tracks 10 shares of AAPL
        mock_conn.fetch.return_value = [{"symbol": "AAPL", "total_qty": Decimal("10")}]
        pool = _mock_pool(mock_conn)

        result = await sync_strategy_positions_from_alpaca(pool)
        assert result == 0
        mock_open.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.data.alpaca_client.get_alpaca_client")
    @patch("src.data.alpaca_client.alpaca_headers")
    @patch("src.data.alpaca_client.alpaca_base_url")
    async def test_api_failure_returns_zero(self, mock_base_url, mock_headers, mock_get_client):
        mock_base_url.return_value = "https://paper-api.alpaca.markets"
        mock_headers.return_value = {}
        mock_get_client.side_effect = Exception("timeout")

        pool = MagicMock()
        result = await sync_strategy_positions_from_alpaca(pool)
        assert result == 0

    @pytest.mark.asyncio
    @patch("src.agents.position_tools.open_strategy_position")
    @patch("src.data.alpaca_client.get_alpaca_client")
    @patch("src.data.alpaca_client.alpaca_headers")
    @patch("src.data.alpaca_client.alpaca_base_url")
    async def test_skips_zero_qty_positions(self, mock_base_url, mock_headers, mock_get_client, mock_open):
        mock_base_url.return_value = "https://paper-api.alpaca.markets"
        mock_headers.return_value = {}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {"symbol": "AAPL", "qty": "0", "avg_entry_price": "150.0"},
        ]
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        result = await sync_strategy_positions_from_alpaca(pool)
        assert result == 0
        mock_open.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.agents.position_tools.open_strategy_position")
    @patch("src.data.alpaca_client.get_alpaca_client")
    @patch("src.data.alpaca_client.alpaca_headers")
    @patch("src.data.alpaca_client.alpaca_base_url")
    async def test_partial_untracked_qty(self, mock_base_url, mock_headers, mock_get_client, mock_open):
        # / alpaca has 10, db tracks 6 -> should insert 4 as untracked
        mock_base_url.return_value = "https://paper-api.alpaca.markets"
        mock_headers.return_value = {}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {"symbol": "MSFT", "qty": "10", "avg_entry_price": "400.0"},
        ]
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [{"symbol": "MSFT", "total_qty": Decimal("6")}]
        pool = _mock_pool(mock_conn)

        result = await sync_strategy_positions_from_alpaca(pool)
        assert result == 1
        mock_open.assert_called_once_with(pool, "untracked", "MSFT", 4.0, 400.0)


# -- reconcile_strategy_positions (bug c) --

class TestReconcileStrategyPositions:
    def _tx_mock(self, mock_conn):
        # / transaction context manager mock
        tx = AsyncMock()
        tx.__aenter__.return_value = None
        tx.__aexit__.return_value = False
        mock_conn.transaction = MagicMock(return_value=tx)

    @pytest.mark.asyncio
    async def test_empty_alpaca_skipped_by_default_guard(self):
        # / baseline: empty alpaca + tracked rows triggers api-glitch guard, no deletes
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {"id": 1, "strategy_id": "s1", "symbol": "NVDA", "qty": Decimal("12")},
        ]
        self._tx_mock(mock_conn)
        pool = _mock_pool(mock_conn)

        await reconcile_strategy_positions(pool, alpaca_map={})
        # / no delete issued because guard kicked in
        mock_conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_alpaca_full_sync_deletes_all(self):
        # / bug c fix: full_sync=true bypasses guard so manual liquidation clears rows
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {"id": 1, "strategy_id": "s1", "symbol": "NVDA", "qty": Decimal("12")},
            {"id": 2, "strategy_id": "s1", "symbol": "META", "qty": Decimal("5")},
            {"id": 3, "strategy_id": "s1", "symbol": "AAPL", "qty": Decimal("8")},
        ]
        self._tx_mock(mock_conn)
        pool = _mock_pool(mock_conn)

        await reconcile_strategy_positions(pool, alpaca_map={}, full_sync=True)
        # / expect one DELETE per stale symbol (3 total)
        delete_calls = [c for c in mock_conn.execute.call_args_list
                        if c.args and "DELETE FROM strategy_positions" in c.args[0]]
        assert len(delete_calls) == 3

    @pytest.mark.asyncio
    async def test_full_sync_partial_keeps_matching(self):
        # / alpaca has NVDA, db has NVDA + META + AAPL; full_sync deletes META + AAPL
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {"id": 1, "strategy_id": "s1", "symbol": "NVDA", "qty": Decimal("12")},
            {"id": 2, "strategy_id": "s1", "symbol": "META", "qty": Decimal("5")},
            {"id": 3, "strategy_id": "s1", "symbol": "AAPL", "qty": Decimal("8")},
        ]
        self._tx_mock(mock_conn)
        pool = _mock_pool(mock_conn)

        await reconcile_strategy_positions(
            pool, alpaca_map={"NVDA": 12.0}, full_sync=True,
        )
        delete_calls = [c for c in mock_conn.execute.call_args_list
                        if c.args and "DELETE FROM strategy_positions" in c.args[0]]
        # / two delete calls: one for META (id=2), one for AAPL (id=3)
        assert len(delete_calls) == 2

    @pytest.mark.asyncio
    async def test_empty_db_noop(self):
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        self._tx_mock(mock_conn)
        pool = _mock_pool(mock_conn)

        await reconcile_strategy_positions(pool, alpaca_map={}, full_sync=True)
        mock_conn.execute.assert_not_called()


# -- fetch_most_recent_open_entry (bug e helper) --

class TestFetchMostRecentOpenEntry:
    @pytest.mark.asyncio
    async def test_returns_entry_from_most_recent_buy(self):
        from datetime import datetime
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "price": Decimal("175.68"), "qty": Decimal("12"),
            "strategy_id": "strategy_011", "created_at": datetime(2026, 4, 7),
        }
        pool = _mock_pool(mock_conn)

        result = await fetch_most_recent_open_entry(pool, "NVDA")
        assert result is not None
        assert result["entry_price"] == 175.68
        assert result["qty"] == 12.0
        assert result["strategy_id"] == "strategy_011"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_buy(self):
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None
        pool = _mock_pool(mock_conn)

        result = await fetch_most_recent_open_entry(pool, "XXXX")
        assert result is None

    @pytest.mark.asyncio
    async def test_handles_null_price(self):
        from datetime import datetime
        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "price": None, "qty": Decimal("5"),
            "strategy_id": None, "created_at": datetime(2026, 4, 1),
        }
        pool = _mock_pool(mock_conn)

        result = await fetch_most_recent_open_entry(pool, "GOOGL")
        assert result is not None
        assert result["entry_price"] is None


# -- backfill_trade_pnl (bug e historical backfill) --

class TestBackfillTradePnl:
    @pytest.mark.asyncio
    async def test_backfills_null_sells(self):
        from datetime import datetime
        mock_conn = AsyncMock()
        # / first fetch: sell rows with null pnl
        mock_conn.fetch.return_value = [
            {"id": 1, "symbol": "NVDA", "qty": Decimal("12"),
             "price": Decimal("185.00"), "created_at": datetime(2026, 4, 10)},
            {"id": 2, "symbol": "META", "qty": Decimal("5"),
             "price": Decimal("580.00"), "created_at": datetime(2026, 4, 10)},
        ]
        # / fetchrow called for each prior-buy lookup
        mock_conn.fetchrow.side_effect = [
            {"price": Decimal("175.68")},  # / NVDA prior buy
            {"price": Decimal("570.50")},  # / META prior buy
        ]
        pool = _mock_pool(mock_conn)

        updated = await backfill_trade_pnl(pool)
        assert updated == 2
        # / two UPDATE executes
        update_calls = [c for c in mock_conn.execute.call_args_list
                        if c.args and "UPDATE trade_log SET pnl" in c.args[0]]
        assert len(update_calls) == 2

    @pytest.mark.asyncio
    async def test_idempotent_on_empty_nulls(self):
        # / no null sells -> 0 updates
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        pool = _mock_pool(mock_conn)

        updated = await backfill_trade_pnl(pool)
        assert updated == 0

    @pytest.mark.asyncio
    async def test_skips_when_no_prior_buy(self):
        from datetime import datetime
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [
            {"id": 1, "symbol": "ORPHAN", "qty": Decimal("1"),
             "price": Decimal("100"), "created_at": datetime(2026, 4, 10)},
        ]
        mock_conn.fetchrow.return_value = None  # / no prior buy
        pool = _mock_pool(mock_conn)

        updated = await backfill_trade_pnl(pool)
        assert updated == 0
        update_calls = [c for c in mock_conn.execute.call_args_list
                        if c.args and "UPDATE trade_log SET pnl" in c.args[0]]
        assert len(update_calls) == 0


# -- sync_trades_from_alpaca pnl computation for sells (bug e) --

class TestSyncTradesFromAlpacaPnl:
    @pytest.mark.asyncio
    @patch("src.data.alpaca_client.get_alpaca_client")
    @patch("src.data.alpaca_client.alpaca_headers")
    @patch("src.data.alpaca_client.alpaca_base_url")
    async def test_sell_gets_pnl_from_prior_buy(self, mock_base_url, mock_headers, mock_get_client):
        mock_base_url.return_value = "https://paper-api.alpaca.markets"
        mock_headers.return_value = {}
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {"id": "sell_1", "symbol": "NVDA", "side": "sell", "filled_qty": "12",
             "filled_avg_price": "185.00", "filled_at": "2026-04-10T14:00:00Z",
             "type": "market", "time_in_force": "day"},
        ]
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []  # / no existing order_ids
        # / fetchrow calls: prior_buy, approved_by_id, approved_proximity, owned_by_real
        mock_conn.fetchrow.side_effect = [{"price": Decimal("175.68")}, None, None, None]
        pool = _mock_pool(mock_conn)

        result = await sync_trades_from_alpaca(pool)
        assert result == 1
        # / verify INSERT was called with pnl positional arg
        insert_call = mock_conn.execute.call_args_list[0]
        # / pnl is $7 param (shifted by fix 4 which added linked_trade_id at $1):
        # / (185 - 175.68) * 12 = 111.84 (float precision noise)
        pnl_arg = insert_call.args[7]
        assert pnl_arg is not None
        assert abs(float(pnl_arg) - 111.84) < 0.01
        # / verify the prior-buy fetchrow sql filtered by created_at (first call).
        prior_buy_call = mock_conn.fetchrow.call_args_list[0]
        assert "created_at <" in prior_buy_call.args[0]
        assert isinstance(prior_buy_call.args[2], datetime)
        assert prior_buy_call.args[2] == datetime(2026, 4, 10, 14, 0, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    @patch("src.data.alpaca_client.get_alpaca_client")
    @patch("src.data.alpaca_client.alpaca_headers")
    @patch("src.data.alpaca_client.alpaca_base_url")
    async def test_buy_still_has_null_pnl(self, mock_base_url, mock_headers, mock_get_client):
        mock_base_url.return_value = "https://paper-api.alpaca.markets"
        mock_headers.return_value = {}
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {"id": "buy_1", "symbol": "NVDA", "side": "buy", "filled_qty": "12",
             "filled_avg_price": "175.68", "filled_at": "2026-04-07T14:00:00Z",
             "type": "market", "time_in_force": "day"},
        ]
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        # / buys skip prior-buy lookup, so only approved_row + owned_by_real
        # / fetchrow calls. both None keeps the test on its happy path.
        mock_conn.fetchrow.return_value = None
        pool = _mock_pool(mock_conn)

        result = await sync_trades_from_alpaca(pool)
        assert result == 1
        insert_call = mock_conn.execute.call_args_list[0]
        # / pnl shifted to $7 after fix 4 added linked_trade_id at $1.
        # / pnl param should be None for buys.
        assert insert_call.args[7] is None

    @pytest.mark.asyncio
    @patch("src.data.alpaca_client.get_alpaca_client")
    @patch("src.data.alpaca_client.alpaca_headers")
    @patch("src.data.alpaca_client.alpaca_base_url")
    async def test_sell_without_prior_buy_has_null_pnl(self, mock_base_url, mock_headers, mock_get_client):
        mock_base_url.return_value = "https://paper-api.alpaca.markets"
        mock_headers.return_value = {}
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {"id": "sell_2", "symbol": "ORPHAN", "side": "sell", "filled_qty": "1",
             "filled_avg_price": "50.00", "filled_at": "2026-04-10T14:00:00Z",
             "type": "market", "time_in_force": "day"},
        ]
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        # / all three fetchrow calls (approved_row, prior_buy, owned_by_real)
        # / return None — sell without an approved row and no prior buy.
        mock_conn.fetchrow.return_value = None
        pool = _mock_pool(mock_conn)

        result = await sync_trades_from_alpaca(pool)
        assert result == 1
        insert_call = mock_conn.execute.call_args_list[0]
        # / pnl shifted to $7 after fix 4 added linked_trade_id at $1.
        assert insert_call.args[7] is None


# -- sync_trades_from_alpaca strategy_positions projection (bug 1a) --

class TestSyncTradesStrategyPositionProjection:
    @pytest.mark.asyncio
    @patch("src.data.alpaca_client.get_alpaca_client")
    @patch("src.data.alpaca_client.alpaca_headers")
    @patch("src.data.alpaca_client.alpaca_base_url")
    async def test_buy_no_real_owner_upserts_untracked(self, mock_base_url, mock_headers, mock_get_client):
        # / bug 1a: buy with no real strategy owner must project an untracked upsert
        mock_base_url.return_value = "https://paper-api.alpaca.markets"
        mock_headers.return_value = {}
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {"id": "ord_buy_untracked", "symbol": "TSLA", "side": "buy", "filled_qty": "5",
             "filled_avg_price": "250.0", "filled_at": "2026-04-09T15:00:00Z",
             "type": "market", "time_in_force": "day"},
        ]
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        # / owned_by_real returns None -> safe to project
        mock_conn.fetchrow.return_value = None
        pool = _mock_pool(mock_conn)

        result = await sync_trades_from_alpaca(pool)
        assert result == 1
        # / buy path: 1 trade_log insert + 1 untracked upsert = 2 execute calls
        assert mock_conn.execute.call_count == 2
        # / second execute is the INSERT ... ON CONFLICT into strategy_positions.
        # / strategy_id is now bound (not a literal) so we can reuse the query
        # / when approved_trades lookup recovers a real strategy attribution.
        upsert_call = mock_conn.execute.call_args_list[1]
        upsert_sql = upsert_call.args[0]
        assert "INSERT INTO strategy_positions" in upsert_sql
        assert "ON CONFLICT" in upsert_sql
        # / first bound param is the strategy_id — falls back to 'untracked'
        assert upsert_call.args[1] == "untracked"

    @pytest.mark.asyncio
    @patch("src.data.alpaca_client.get_alpaca_client")
    @patch("src.data.alpaca_client.alpaca_headers")
    @patch("src.data.alpaca_client.alpaca_base_url")
    async def test_buy_with_real_owner_skips_strategy_positions(self, mock_base_url, mock_headers, mock_get_client):
        # / bug 1a: when a real strategy already owns the symbol, do not touch strategy_positions
        mock_base_url.return_value = "https://paper-api.alpaca.markets"
        mock_headers.return_value = {}
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {"id": "ord_buy_owned", "symbol": "AAPL", "side": "buy", "filled_qty": "10",
             "filled_avg_price": "180.0", "filled_at": "2026-04-09T15:00:00Z",
             "type": "market", "time_in_force": "day"},
        ]
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        # / fetchrow calls: approved_by_id, approved_proximity, owned_by_real
        mock_conn.fetchrow.side_effect = [None, None, {"?column?": 1}]
        pool = _mock_pool(mock_conn)

        result = await sync_trades_from_alpaca(pool)
        assert result == 1
        # / only the trade_log insert should run — strategy_positions untouched
        assert mock_conn.execute.call_count == 1
        insert_sql = mock_conn.execute.call_args_list[0].args[0]
        assert "INSERT INTO trade_log" in insert_sql

    @pytest.mark.asyncio
    @patch("src.data.alpaca_client.get_alpaca_client")
    @patch("src.data.alpaca_client.alpaca_headers")
    @patch("src.data.alpaca_client.alpaca_base_url")
    async def test_sell_no_real_owner_updates_and_deletes_untracked(self, mock_base_url, mock_headers, mock_get_client):
        # / bug 1a: sell path must run update qty then delete-if-zero for untracked row
        mock_base_url.return_value = "https://paper-api.alpaca.markets"
        mock_headers.return_value = {}
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {"id": "ord_sell_untracked", "symbol": "TSLA", "side": "sell", "filled_qty": "5",
             "filled_avg_price": "260.0", "filled_at": "2026-04-10T15:00:00Z",
             "type": "market", "time_in_force": "day"},
        ]
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []
        # / fetchrow calls: prior_buy, approved_by_id, approved_proximity, owned_by_real
        mock_conn.fetchrow.side_effect = [{"price": Decimal("250.00")}, None, None, None]
        pool = _mock_pool(mock_conn)

        result = await sync_trades_from_alpaca(pool)
        assert result == 1
        # / sell path: trade_log insert + UPDATE qty + DELETE if <=0 = 3 execute calls
        assert mock_conn.execute.call_count >= 3
        update_call = mock_conn.execute.call_args_list[1]
        delete_call = mock_conn.execute.call_args_list[2]
        update_sql = update_call.args[0]
        delete_sql = delete_call.args[0]
        assert "UPDATE strategy_positions" in update_sql
        # / strategy_id is a bound parameter now; no approved_trades row means
        # / we fall back to 'untracked' for both the UPDATE and the DELETE.
        assert "untracked" in (str(update_call.args[-1]) + str(delete_call.args))
        assert "DELETE FROM strategy_positions" in delete_sql
        assert "qty <= 0" in delete_sql

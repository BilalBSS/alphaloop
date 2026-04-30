# / tests for api/llm cost tracker

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.data import cost_tracker


def _mock_pool():
    # / asyncpg pool -> async context manager -> connection mock pattern
    pool = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


@pytest.fixture(autouse=True)
def _reset_cost_state():
    # / clear in-memory accumulator before and after every test so they are independent
    cost_tracker._daily_costs.clear()
    yield
    cost_tracker._daily_costs.clear()


class TestTrackLLMCost:
    def test_increments_call_count_and_tokens(self):
        cost_tracker.track_llm_cost("groq", "llama-3.3-70b", 1000, 500)
        summary = cost_tracker.get_daily_summary()
        assert "groq" in summary
        assert summary["groq"]["call_count"] == 1
        assert summary["groq"]["tokens_in"] == 1000
        assert summary["groq"]["tokens_out"] == 500

    def test_free_provider_cost_zero(self):
        # / groq is free — cost must stay at 0 regardless of token volume
        cost_tracker.track_llm_cost("groq", "llama-3.3-70b", 1_000_000, 1_000_000)
        summary = cost_tracker.get_daily_summary()
        assert summary["groq"]["cost"] == pytest.approx(0.0)

    def test_deepseek_chat_cost_exact_formula(self):
        # / hand computed: (1M * 0.14 + 500k * 0.28) / 1M = 0.14 + 0.14 = 0.28
        cost_tracker.track_llm_cost("deepseek", "deepseek-chat", 1_000_000, 500_000)
        summary = cost_tracker.get_daily_summary()
        assert summary["deepseek"]["cost"] == pytest.approx(0.28)

    def test_deepseek_reasoner_cost_exact_formula(self):
        # / hand computed: (100k * 0.55 + 100k * 2.19) / 1M = 0.055 + 0.219 = 0.274
        cost_tracker.track_llm_cost("deepseek", "deepseek-reasoner", 100_000, 100_000)
        summary = cost_tracker.get_daily_summary()
        assert summary["deepseek"]["cost"] == pytest.approx(0.274)

    def test_unknown_model_and_provider_zero_cost(self):
        # / no rates in map -> fall through default (0,0) tuple, cost stays 0
        cost_tracker.track_llm_cost("unknown_provider", "unknown_model", 1000, 500)
        summary = cost_tracker.get_daily_summary()
        assert summary["unknown_provider"]["cost"] == pytest.approx(0.0)
        assert summary["unknown_provider"]["call_count"] == 1

    def test_accumulates_across_calls(self):
        cost_tracker.track_llm_cost("deepseek", "deepseek-chat", 500_000, 250_000)
        cost_tracker.track_llm_cost("deepseek", "deepseek-chat", 500_000, 250_000)
        summary = cost_tracker.get_daily_summary()
        assert summary["deepseek"]["call_count"] == 2
        assert summary["deepseek"]["tokens_in"] == 1_000_000
        assert summary["deepseek"]["tokens_out"] == 500_000
        # / 2 * ((500k*0.14 + 250k*0.28)/1M) = 2 * 0.14 = 0.28
        assert summary["deepseek"]["cost"] == pytest.approx(0.28)


class TestTrackAPICall:
    def test_increments_call_count(self):
        cost_tracker.track_api_call("finnhub")
        summary = cost_tracker.get_daily_summary()
        assert summary["finnhub"]["call_count"] == 1

    def test_no_token_or_cost_effect(self):
        cost_tracker.track_api_call("coingecko")
        summary = cost_tracker.get_daily_summary()
        assert summary["coingecko"]["tokens_in"] == 0
        assert summary["coingecko"]["tokens_out"] == 0
        assert summary["coingecko"]["cost"] == 0.0

    def test_multiple_sources_are_separate(self):
        cost_tracker.track_api_call("finnhub")
        cost_tracker.track_api_call("yfinance")
        cost_tracker.track_api_call("finnhub")
        summary = cost_tracker.get_daily_summary()
        assert summary["finnhub"]["call_count"] == 2
        assert summary["yfinance"]["call_count"] == 1


class TestGetDailySummary:
    def test_empty_when_no_calls(self):
        summary = cost_tracker.get_daily_summary()
        assert summary == {}

    def test_only_returns_today(self):
        # / inject a stale (yesterday) entry directly — must NOT appear in today's summary
        from datetime import timedelta
        yesterday = date.today() - timedelta(days=1)
        cost_tracker._daily_costs[(yesterday, "stale_source")] = {
            "call_count": 5, "tokens_in": 0, "tokens_out": 0, "cost": 0.0,
        }
        cost_tracker.track_api_call("today_source")
        summary = cost_tracker.get_daily_summary()
        assert "today_source" in summary
        assert "stale_source" not in summary


class TestFlushToDB:
    @pytest.mark.asyncio
    async def test_empty_state_writes_nothing(self):
        pool, conn = _mock_pool()
        written = await cost_tracker.flush_to_db(pool)
        assert written == 0
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_flushes_and_clears_state(self):
        cost_tracker.track_llm_cost("deepseek", "deepseek-chat", 1000, 500)
        cost_tracker.track_api_call("finnhub")
        pool, conn = _mock_pool()
        written = await cost_tracker.flush_to_db(pool)
        # / 2 distinct sources -> 2 inserts
        assert written == 2
        assert conn.execute.call_count == 2
        # / state cleared after flush
        assert cost_tracker._daily_costs == {}

    @pytest.mark.asyncio
    async def test_cost_converted_to_decimal_with_6_digits(self):
        cost_tracker.track_llm_cost("deepseek", "deepseek-chat", 1_000_000, 500_000)
        pool, conn = _mock_pool()
        await cost_tracker.flush_to_db(pool)
        # / last positional arg is the cost Decimal
        cost_arg = conn.execute.call_args.args[-1]
        assert isinstance(cost_arg, Decimal)
        # / 0.28 rounded to 6 places
        assert cost_arg == Decimal("0.28")

    @pytest.mark.asyncio
    async def test_one_failure_does_not_stop_others(self):
        cost_tracker.track_api_call("source_a")
        cost_tracker.track_api_call("source_b")
        pool = MagicMock()
        conn = MagicMock()
        # / first insert raises, second succeeds
        conn.execute = AsyncMock(side_effect=[Exception("conflict"), "INSERT 0 1"])
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = MagicMock(return_value=cm)
        written = await cost_tracker.flush_to_db(pool)
        # / only the successful one counts
        assert written == 1
        assert conn.execute.call_count == 2
        # / state still cleared (flush is idempotent-by-design)
        assert cost_tracker._daily_costs == {}

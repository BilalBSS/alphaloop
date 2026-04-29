# / tests for phase 5 activation health metrics

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.phase5_metrics import (
    Phase5Metrics,
    compute_phase5_metrics,
)


def _mock_pool(fetchrow_side_effect=None, fetchval_side_effect=None):
    # / standard asyncpg pool mock pattern — MagicMock pool, AsyncMock acquire+conn
    conn = MagicMock()
    conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect or [])
    conn.fetchval = AsyncMock(side_effect=fetchval_side_effect or [])
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


class TestDataclass:

    def test_as_dict_round_trip(self):
        m = Phase5Metrics(
            days_since_last_evolution_kill=3,
            evolution_kills_30d=2,
            strategies_with_brier=5,
            total_strategies=16,
            cerebras_calls_7d=40,
            groq_calls_7d=400,
            cerebras_call_pct_7d=0.0909,
            established_wiki_count=20,
            total_wiki_count=75,
            equity_regime_diversity_7d=3,
            crypto_regime_diversity_7d=2,
            sector_regime_diversity_7d=4,
            computed_at="2026-04-18T12:00:00+00:00",
        )
        d = m.as_dict()
        assert d["strategies_with_brier"] == 5
        assert d["cerebras_call_pct_7d"] == 0.0909

    def test_success_criteria_all_pass(self):
        m = Phase5Metrics(
            days_since_last_evolution_kill=3, evolution_kills_30d=2,
            strategies_with_brier=5, total_strategies=16,
            cerebras_calls_7d=40, groq_calls_7d=400, cerebras_call_pct_7d=0.09,
            established_wiki_count=20, total_wiki_count=75,
            equity_regime_diversity_7d=3, crypto_regime_diversity_7d=2,
            sector_regime_diversity_7d=4,
            computed_at="2026-04-18T12:00:00+00:00",
        )
        assert m.all_pass() is True
        criteria = m.success_criteria()
        assert all(criteria.values())

    def test_success_criteria_fail_on_no_cerebras(self):
        m = Phase5Metrics(
            days_since_last_evolution_kill=3, evolution_kills_30d=2,
            strategies_with_brier=5, total_strategies=16,
            cerebras_calls_7d=0, groq_calls_7d=400, cerebras_call_pct_7d=0.0,
            established_wiki_count=20, total_wiki_count=75,
            equity_regime_diversity_7d=3, crypto_regime_diversity_7d=2,
            sector_regime_diversity_7d=4,
            computed_at="2026-04-18T12:00:00+00:00",
        )
        assert m.all_pass() is False
        assert m.success_criteria()["cerebras_nonzero_7d"] is False

    def test_success_criteria_fail_on_low_brier(self):
        m = Phase5Metrics(
            days_since_last_evolution_kill=3, evolution_kills_30d=2,
            strategies_with_brier=1, total_strategies=16,
            cerebras_calls_7d=40, groq_calls_7d=400, cerebras_call_pct_7d=0.09,
            established_wiki_count=20, total_wiki_count=75,
            equity_regime_diversity_7d=3, crypto_regime_diversity_7d=2,
            sector_regime_diversity_7d=4,
            computed_at="2026-04-18T12:00:00+00:00",
        )
        assert m.success_criteria()["brier_populated_3plus"] is False


class TestComputeWithMockedPool:

    @pytest.mark.asyncio
    async def test_all_queries_return_zero_fresh_slate(self):
        # / fresh-slate scenario: no evolution, no brier, no cerebras, no established wiki
        # / matches the state right after Phase 5 Step 1 clean slate
        # / all queries return None -> metrics coerce to 0
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value=None)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)

        m = await compute_phase5_metrics(pool)
        assert isinstance(m, Phase5Metrics)
        assert m.evolution_kills_30d == 0
        assert m.strategies_with_brier == 0
        assert m.cerebras_calls_7d == 0
        assert m.groq_calls_7d == 0
        assert m.cerebras_call_pct_7d == 0.0
        assert m.established_wiki_count == 0
        assert m.all_pass() is False

    @pytest.mark.asyncio
    async def test_handles_query_error_gracefully(self):
        # / if a table doesn't exist, the metric should be 0 not raise
        conn = MagicMock()
        conn.fetchrow = AsyncMock(side_effect=Exception("table does not exist"))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)

        m = await compute_phase5_metrics(pool)
        # / should not raise, all values should be zero/None
        assert m.days_since_last_evolution_kill is None
        assert m.evolution_kills_30d == 0
        assert m.strategies_with_brier == 0

    @pytest.mark.asyncio
    async def test_cerebras_pct_calculation(self):
        # / cerebras=40, groq=400 -> pct = 40/(40+400) = 0.0909
        # / _safe_fetch_val uses fetchrow; return a one-element tuple-like for each call
        conn = MagicMock()
        # / order matches _evolution_stats, _brier_stats, _llm_split_stats, _wiki_stats, _regime_diversity x3
        conn.fetchrow = AsyncMock(side_effect=[
            None,  # last_kill_row for evolution (no kill)
            (5,),    # kills_30d
            (3,),    # with_brier
            (16,),   # total_strat
            (40,),   # cerebras_calls_7d
            (400,),  # groq_calls_7d
            (20,),   # established
            (75,),   # total_wiki
            (3,),    # eq_div
            (2,),    # cr_div
            (4,),    # sector_div
        ])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=None)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)

        m = await compute_phase5_metrics(pool)
        assert m.cerebras_calls_7d == 40
        assert m.groq_calls_7d == 400
        # / 40 / (40+400) = 0.0909...
        assert m.cerebras_call_pct_7d == pytest.approx(0.0909, abs=0.001)

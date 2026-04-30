# / tier 3 priority 1: strategy decay detection (rolling sharpe + cusum)
# / drives demote/kill recommendations for live strategies. formula bugs here mean
# / evolution kills good strategies or keeps dying ones around.

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from src.analysis.strategy_decay import (
    MIN_TRADES,
    SHARPE_THRESHOLD,
    DecaySignal,
    check_all_strategies,
    check_strategy_decay,
)


def _mock_pool_with_rows(rows):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _pnl_rows(pnls):
    # / convention: pnls[0] = oldest, pnls[-1] = newest.
    # / returns rows sorted DESC by created_at (newest first) to match the SQL
    # / `ORDER BY created_at DESC` the module uses. inside check_strategy_decay
    # / `reversed(rows)` reverses back to chronological order.
    base = datetime.now()
    n = len(pnls)
    rows = [
        {"pnl": pnl, "created_at": base - timedelta(days=n - i)}
        for i, pnl in enumerate(pnls)
    ]
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows


class TestDecaySignal:
    def test_constructs(self):
        s = DecaySignal(strategy_id="x", rolling_sharpe=1.0,
                        days_below_threshold=0, cusum_triggered=False,
                        recommendation="ok")
        assert s.strategy_id == "x"
        assert s.recommendation == "ok"


class TestCheckStrategyDecay:
    @pytest.mark.asyncio
    async def test_returns_none_below_min_trades(self):
        # / fewer than MIN_TRADES = no signal (not enough data to judge)
        rows = _pnl_rows([100.0] * (MIN_TRADES - 1))
        pool, _ = _mock_pool_with_rows(rows)
        result = await check_strategy_decay(pool, "strat_001")
        assert result is None

    @pytest.mark.asyncio
    async def test_consistent_profit_returns_ok(self):
        # / steady positive pnl with low noise → high sharpe, recommendation ok
        rng = np.random.default_rng(1)
        pnls = list(rng.normal(50.0, 5.0, 50))
        rows = _pnl_rows(pnls)
        pool, _ = _mock_pool_with_rows(rows)
        result = await check_strategy_decay(pool, "strat_ok")
        assert result is not None
        assert result.rolling_sharpe > SHARPE_THRESHOLD
        assert result.recommendation == "ok"

    @pytest.mark.asyncio
    async def test_persistent_losses_recommend_kill(self):
        # / noisy losses (mean negative, nonzero std) → negative sharpe → kill
        rng = np.random.default_rng(3)
        pnls = list(rng.normal(-50.0, 20.0, 40))
        rows = _pnl_rows(pnls)
        pool, _ = _mock_pool_with_rows(rows)
        result = await check_strategy_decay(pool, "dead")
        assert result is not None
        assert result.rolling_sharpe < 0
        assert result.recommendation == "kill"

    @pytest.mark.asyncio
    async def test_zero_std_returns_sharpe_zero(self):
        # / flat pnl (every trade identical) → std=0 guard → rolling_sharpe=0.0
        pnls = [10.0] * 30
        rows = _pnl_rows(pnls)
        pool, _ = _mock_pool_with_rows(rows)
        result = await check_strategy_decay(pool, "flat")
        assert result is not None
        assert result.rolling_sharpe == 0.0

    @pytest.mark.asyncio
    async def test_cusum_triggers_on_large_regime_shift(self):
        # / long stable-positive series followed by a sharp downturn. the cusum
        # / algorithm accumulates downward deviation from target_mean; needs the
        # / negative segment to be sustained AND much larger than the positive
        # / variance for the rolling sum to exceed threshold.
        rng = np.random.default_rng(8)
        good = list(rng.normal(100.0, 5.0, 60))
        bad = list(rng.normal(-400.0, 5.0, 30))
        rows = _pnl_rows(good + bad)
        pool, _ = _mock_pool_with_rows(rows)
        result = await check_strategy_decay(pool, "regime_shift")
        assert result is not None
        assert result.cusum_triggered is True

    @pytest.mark.asyncio
    async def test_recommendation_demote_when_sharpe_weak_and_days_below(self):
        # / noisy near-zero recent pnl → low rolling sharpe, recommendation
        # / should NOT be "ok" (monitor/demote/kill all acceptable).
        rng = np.random.default_rng(11)
        pnls = list(rng.normal(-0.5, 50.0, 60))  # / slight negative drift + high noise
        rows = _pnl_rows(pnls)
        pool, _ = _mock_pool_with_rows(rows)
        result = await check_strategy_decay(pool, "drifting")
        assert result is not None
        assert result.rolling_sharpe < SHARPE_THRESHOLD
        assert result.recommendation != "ok"

    @pytest.mark.asyncio
    async def test_queries_correct_strategy_id(self):
        # / ensure the sql bind param matches the input strategy_id
        pnls = [50.0] * MIN_TRADES + [20.0]
        rows = _pnl_rows(pnls)
        pool, conn = _mock_pool_with_rows(rows)
        await check_strategy_decay(pool, "strategy_007")
        args = conn.fetch.call_args.args
        assert args[1] == "strategy_007"


class TestCheckAllStrategies:
    @pytest.mark.asyncio
    async def test_empty_pool_returns_empty_list(self):
        pool = MagicMock()
        fake_pool = MagicMock()
        fake_pool.list_by_status = MagicMock(return_value=[])
        out = await check_all_strategies(pool, fake_pool)
        assert out == []

    @pytest.mark.asyncio
    async def test_only_non_ok_signals_returned(self):
        # / two live strategies: one healthy (ok), one decaying (kill)
        pool = MagicMock()

        def make_entry(sid: str):
            entry = MagicMock()
            entry.strategy = MagicMock()
            entry.strategy.strategy_id = sid
            return entry

        fake_pool = MagicMock()
        fake_pool.list_by_status = MagicMock(
            return_value=[make_entry("good"), make_entry("bad")],
        )

        async def fake_check(pool_arg, sid):
            if sid == "good":
                return DecaySignal(
                    strategy_id=sid, rolling_sharpe=1.5,
                    days_below_threshold=0, cusum_triggered=False,
                    recommendation="ok",
                )
            return DecaySignal(
                strategy_id=sid, rolling_sharpe=-0.5,
                days_below_threshold=20, cusum_triggered=True,
                recommendation="kill",
            )

        import src.analysis.strategy_decay as mod
        orig = mod.check_strategy_decay
        mod.check_strategy_decay = fake_check
        try:
            out = await check_all_strategies(pool, fake_pool)
        finally:
            mod.check_strategy_decay = orig
        assert len(out) == 1
        assert out[0].strategy_id == "bad"

    @pytest.mark.asyncio
    async def test_none_signals_filtered(self):
        # / strategy with <MIN_TRADES returns None → filtered out silently
        pool = MagicMock()
        entry = MagicMock()
        entry.strategy = MagicMock()
        entry.strategy.strategy_id = "new"
        fake_pool = MagicMock()
        fake_pool.list_by_status = MagicMock(return_value=[entry])

        import src.analysis.strategy_decay as mod
        orig = mod.check_strategy_decay
        mod.check_strategy_decay = AsyncMock(return_value=None)
        try:
            out = await check_all_strategies(pool, fake_pool)
        finally:
            mod.check_strategy_decay = orig
        assert out == []

# / phase 6 step 10: kelly-weighted capital allocator tests

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents import capital_allocator
from src.agents.capital_allocator import (
    MIN_TRADES_FOR_KELLY,
    _build_allocations,
    _rank_weight,
)


def test_rank_weight_top_quartile():
    assert _rank_weight(0.0) == 2.0
    assert _rank_weight(0.25) == 2.0


def test_rank_weight_middle():
    assert _rank_weight(0.4) == 1.0
    assert _rank_weight(0.74) == 1.0


def test_rank_weight_bottom_quartile():
    assert _rank_weight(0.75) == 0.5
    assert _rank_weight(1.0) == 0.5


def test_build_allocations_empty_returns_empty():
    assert _build_allocations([], max_position_pct=0.04) == []


def test_build_allocations_sorts_by_composite_score():
    rows = [
        {"strategy_id": "s_low",  "composite_score": 30.0, "kelly_fraction": 0.2, "trade_count": 50},
        {"strategy_id": "s_high", "composite_score": 90.0, "kelly_fraction": 0.2, "trade_count": 50},
        {"strategy_id": "s_mid",  "composite_score": 60.0, "kelly_fraction": 0.2, "trade_count": 50},
    ]
    allocs = _build_allocations(rows, max_position_pct=0.04)
    ids_by_rank = {a.strategy_id: a.rank_weight for a in allocs}
    # / top scorer gets 2.0, bottom gets 0.5
    assert ids_by_rank["s_high"] == 2.0
    assert ids_by_rank["s_low"] == 0.5


def test_build_allocations_under_sampled_uses_half_cap():
    rows = [{
        "strategy_id": "s1",
        "composite_score": 80.0,
        "kelly_fraction": 0.5,
        "trade_count": MIN_TRADES_FOR_KELLY - 1,
    }]
    allocs = _build_allocations(rows, max_position_pct=0.04)
    assert allocs[0].allocated_weight == pytest.approx(0.02)


def test_build_allocations_kelly_engages_with_enough_trades():
    rows = [{
        "strategy_id": "s_top",
        "composite_score": 90.0,
        "kelly_fraction": 0.25,
        "trade_count": MIN_TRADES_FOR_KELLY,
    }, {
        "strategy_id": "s_bot",
        "composite_score": 20.0,
        "kelly_fraction": 0.25,
        "trade_count": MIN_TRADES_FOR_KELLY,
    }]
    allocs = _build_allocations(rows, max_position_pct=0.04)
    weights = {a.strategy_id: a.allocated_weight for a in allocs}
    # / top quartile (rank_weight=2.0) gets more than bottom quartile (rank_weight=0.5)
    assert weights["s_top"] > weights["s_bot"]


def test_build_allocations_clamps_upper_bound():
    rows = [{
        "strategy_id": "s_aggressive",
        "composite_score": 99.0,
        "kelly_fraction": 1.0,  # / max kelly + top quartile would push way above cap
        "trade_count": 100,
    }]
    allocs = _build_allocations(rows, max_position_pct=0.04)
    # / clamped at 3 × max_position_pct
    assert allocs[0].allocated_weight <= 0.04 * 3 + 1e-9


def test_build_allocations_clamps_lower_bound():
    rows = [{
        "strategy_id": "s_tiny",
        "composite_score": 1.0,
        "kelly_fraction": 0.01,
        "trade_count": 100,
    }]
    allocs = _build_allocations(rows, max_position_pct=0.04)
    # / floor at 0.25 × max_position_pct
    assert allocs[0].allocated_weight >= 0.04 * 0.25 - 1e-9


def test_build_allocations_unscored_gets_floor():
    rows = [
        {"strategy_id": "s_scored",   "composite_score": 80.0, "kelly_fraction": 0.2, "trade_count": 50},
        {"strategy_id": "s_unscored", "composite_score": None, "kelly_fraction": 0.5, "trade_count": 50},
    ]
    allocs = _build_allocations(rows, max_position_pct=0.04)
    by_id = {a.strategy_id: a for a in allocs}
    assert by_id["s_unscored"].allocated_weight == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_get_allocation_returns_default_without_row():
    # / no row means allocator hasn't written for this strategy yet; fall back
    # / to full default so the first week of trading isn't penalized.
    pool = MagicMock()
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    w = await capital_allocator.get_allocation(pool, "s_missing", max_position_pct_default=0.04)
    assert w == pytest.approx(0.04)


@pytest.mark.asyncio
async def test_get_allocation_returns_row_value_when_present():
    pool = MagicMock()
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"allocated_weight": 0.065})
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    w = await capital_allocator.get_allocation(pool, "s1", max_position_pct_default=0.04)
    assert w == pytest.approx(0.065)


@pytest.mark.asyncio
async def test_get_allocation_with_null_pool_returns_default():
    w = await capital_allocator.get_allocation(None, "s1", max_position_pct_default=0.04)
    assert w == pytest.approx(0.04)

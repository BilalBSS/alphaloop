from __future__ import annotations

from src.agents.capital_allocator import dynamic_caps

CFG = {
    "min_cash_reserve_pct": 0.10,
    "max_position_pct": 0.08,
    "min_position_pct": 0.02,
    "max_exposure_per_strategy_pct": 0.40,
    "min_exposure_per_strategy_pct": 0.10,
    "max_positions_per_strategy": 6,
}


def test_thin_pool_hits_per_strat_ceiling():
    caps = dynamic_caps(active_count=2, cfg=CFG)
    assert caps.per_strategy_pct == 0.40
    assert 0.02 <= caps.per_position_pct <= 0.08


def test_dense_pool_hits_per_strat_floor():
    caps = dynamic_caps(active_count=30, cfg=CFG)
    assert caps.per_strategy_pct == 0.10
    assert caps.per_position_pct >= 0.02


def test_mid_count_scales_linearly():
    caps = dynamic_caps(active_count=5, cfg=CFG)
    expected_per_strat = 0.18
    assert abs(caps.per_strategy_pct - expected_per_strat) < 1e-6


def test_zero_active_treated_as_one():
    caps = dynamic_caps(active_count=0, cfg=CFG)
    assert caps.active_count == 1
    assert caps.per_strategy_pct == 0.40
    assert caps.per_position_pct == round(0.40 / 6, 5)


def test_per_position_floor_respected():
    cfg = {**CFG, "max_positions_per_strategy": 30}
    caps = dynamic_caps(active_count=30, cfg=cfg)
    assert caps.per_position_pct == 0.02


def test_per_position_ceiling_respected():
    cfg = {**CFG, "max_positions_per_strategy": 1}
    caps = dynamic_caps(active_count=2, cfg=cfg)
    assert caps.per_position_pct == 0.08

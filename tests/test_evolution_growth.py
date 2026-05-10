from __future__ import annotations

from src.evolution.evolution_engine import EvolutionEngine
from src.strategies.base_strategy import ConfigDrivenStrategy
from src.strategies.strategy_pool import StrategyPool, StrategyScore


def _strat(sid: str) -> ConfigDrivenStrategy:
    return ConfigDrivenStrategy({
        "id": sid,
        "name": sid,
        "version": 1,
        "asset_class": "stocks",
        "universe": "all_stocks",
        "tier": "sector",
        "entry_conditions": {
            "operator": "AND",
            "signals": [{"indicator": "rsi", "condition": "below", "threshold": 30, "period": 14}],
        },
        "exit_conditions": {"stop_loss": {"type": "fixed_pct", "pct": 0.05}},
        "position_sizing": {"method": "fixed_pct", "max_position_pct": 0.03},
        "metadata": {"status": "paper_trading"},
    })


def _score(sid: str, sharpe: float, win_rate: float = 0.6, dd: float = 0.05) -> StrategyScore:
    return StrategyScore(strategy_id=sid, sharpe_ratio=sharpe, win_rate=win_rate, max_drawdown=dd)


def _engine(spawn_from_paper: bool, min_composite: float = 2.0) -> EvolutionEngine:
    return EvolutionEngine(risk_limits={
        "evolution": {
            "spawn_from_paper": spawn_from_paper,
            "spawn_min_composite": min_composite,
        },
    })


def test_paper_blocked_when_disabled():
    pool = StrategyPool()
    pool.add(_strat("s1"), status="paper_trading")
    pool.update_score("s1", _score("s1", sharpe=10.0))
    engine = _engine(spawn_from_paper=False)
    assert engine._eligible_spawn_parents(pool) == []


def test_paper_eligible_when_enabled_and_above_threshold():
    pool = StrategyPool()
    pool.add(_strat("s1"), status="paper_trading")
    pool.update_score("s1", _score("s1", sharpe=10.0))
    engine = _engine(spawn_from_paper=True, min_composite=2.0)
    parents = engine._eligible_spawn_parents(pool)
    assert len(parents) == 1
    assert parents[0].strategy.strategy_id == "s1"


def test_paper_below_min_composite_filtered_out():
    pool = StrategyPool()
    pool.add(_strat("s_lo"), status="paper_trading")
    pool.update_score("s_lo", _score("s_lo", sharpe=0.5))
    pool.add(_strat("s_hi"), status="paper_trading")
    pool.update_score("s_hi", _score("s_hi", sharpe=10.0))
    engine = _engine(spawn_from_paper=True, min_composite=2.0)
    parents = engine._eligible_spawn_parents(pool)
    ids = {p.strategy.strategy_id for p in parents}
    assert ids == {"s_hi"}


def test_unscored_paper_skipped():
    pool = StrategyPool()
    pool.add(_strat("s1"), status="paper_trading")
    engine = _engine(spawn_from_paper=True)
    assert engine._eligible_spawn_parents(pool) == []


def test_live_always_eligible():
    pool = StrategyPool()
    pool.add(_strat("live1"), status="live")
    pool.update_score("live1", _score("live1", sharpe=0.5))
    engine = _engine(spawn_from_paper=False)
    parents = engine._eligible_spawn_parents(pool)
    assert {p.strategy.strategy_id for p in parents} == {"live1"}

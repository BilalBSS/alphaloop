from __future__ import annotations

from pathlib import Path

import pytest

from src.evolution.strategy_mutator import _next_sequential_id


def test_sequential_id_picks_next_after_max(tmp_path: Path):
    (tmp_path / "strategy_001.json").write_text("{}", encoding="utf-8")
    (tmp_path / "strategy_005.json").write_text("{}", encoding="utf-8")
    (tmp_path / "strategy_029.json").write_text("{}", encoding="utf-8")
    assert _next_sequential_id(tmp_path) == "strategy_030"


def test_sequential_id_ignores_non_sequential(tmp_path: Path):
    (tmp_path / "strategy_001.json").write_text("{}", encoding="utf-8")
    (tmp_path / "strategy_a8f3b21c.json").write_text("{}", encoding="utf-8")
    (tmp_path / "strategy_x.json").write_text("{}", encoding="utf-8")
    assert _next_sequential_id(tmp_path) == "strategy_002"


def test_sequential_id_falls_back_to_uuid_when_empty(tmp_path: Path):
    sid = _next_sequential_id(tmp_path)
    assert sid.startswith("strategy_")
    assert len(sid.split("_", 1)[1]) == 8


def test_sequential_id_zero_pads(tmp_path: Path):
    (tmp_path / "strategy_007.json").write_text("{}", encoding="utf-8")
    assert _next_sequential_id(tmp_path) == "strategy_008"


def test_sequential_id_three_digit_overflow(tmp_path: Path):
    (tmp_path / "strategy_999.json").write_text("{}", encoding="utf-8")
    assert _next_sequential_id(tmp_path) == "strategy_1000"


# / kill fairness gates

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

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
        "entry_conditions": {
            "operator": "AND",
            "signals": [{"indicator": "rsi", "condition": "below", "threshold": 30, "period": 14}],
        },
        "exit_conditions": {"stop_loss": {"type": "fixed_pct", "pct": 0.05}},
        "position_sizing": {"method": "fixed_pct", "max_position_pct": 0.03},
        "metadata": {"status": "paper_trading"},
    })


def _build_pool(strategies: list[tuple[str, int, int, float]]) -> StrategyPool:
    pool = StrategyPool()
    now = datetime.now(timezone.utc)
    for sid, days_alive, trades, sharpe in strategies:
        pool.add(_strat(sid), status="paper_trading")
        pool.update_score(sid, StrategyScore(
            strategy_id=sid, sharpe_ratio=sharpe, win_rate=0.5,
            max_drawdown=0.1, total_trades=trades,
        ))
        entry = pool.get(sid)
        entry.status_changed_at = now - timedelta(days=days_alive)
    return pool


def _engine(min_trades: int = 10, min_days: int = 7) -> EvolutionEngine:
    return EvolutionEngine(risk_limits={
        "evolution": {
            "min_trades_before_kill": min_trades,
            "min_days_before_kill": min_days,
        },
    })


@pytest.mark.asyncio
async def test_kill_skips_young_strategy_under_min_days():
    # / 5 strats, all bottom-quartile candidates, but one is too young
    strategies = [
        ("old_loser_a", 30, 20, -1.0),
        ("old_loser_b", 30, 20, -0.8),
        ("old_loser_c", 30, 20, -0.5),
        ("young_loser", 3, 20, -2.0),
        ("winner",     30, 20, 5.0),
    ]
    pool_obj = _build_pool(strategies)
    db_pool = MagicMock()
    db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
    db_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    eng = _engine(min_trades=10, min_days=7)
    eng._generation = 1

    from unittest.mock import patch
    with patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock), \
         patch("src.evolution.evolution_engine.update_evolution_mutation_by_mutant", new_callable=AsyncMock):
        killed = await eng._kill_bottom_quartile(db_pool, 1, pool_obj, {"killed": []})
    killed_ids = {k["id"] for k in killed}
    assert "young_loser" not in killed_ids


@pytest.mark.asyncio
async def test_kill_skips_strategy_under_min_trades():
    strategies = [
        ("low_trade",   30, 5,  -2.0),
        ("ok_a",        30, 20, -1.0),
        ("ok_b",        30, 20, -0.8),
        ("ok_c",        30, 20, -0.5),
        ("winner",      30, 20, 5.0),
    ]
    pool_obj = _build_pool(strategies)
    db_pool = MagicMock()
    db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
    db_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    eng = _engine(min_trades=10, min_days=7)
    eng._generation = 1

    from unittest.mock import patch
    with patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock), \
         patch("src.evolution.evolution_engine.update_evolution_mutation_by_mutant", new_callable=AsyncMock):
        killed = await eng._kill_bottom_quartile(db_pool, 1, pool_obj, {"killed": []})
    killed_ids = {k["id"] for k in killed}
    assert "low_trade" not in killed_ids

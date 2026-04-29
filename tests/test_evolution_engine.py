# / tests for evolution_engine

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.evolution.evolution_engine import EvolutionEngine
from src.strategies.base_strategy import ConfigDrivenStrategy
from src.strategies.strategy_pool import (
    StrategyPool,
    StrategyScore,
)


def _make_strategy(sid: str, name: str = "test", generation: int = 1, status: str = "backtest_pending", paper_days: int = 0) -> ConfigDrivenStrategy:
    return ConfigDrivenStrategy({
        "id": sid,
        "name": name,
        "version": 1,
        "asset_class": "stocks",
        "universe": "all_stocks",
        "entry_conditions": {
            "operator": "AND",
            "signals": [{"indicator": "rsi", "condition": "below", "threshold": 30, "period": 14}],
        },
        "exit_conditions": {"stop_loss": {"type": "fixed_pct", "pct": 0.05}},
        "position_sizing": {"method": "fixed_pct", "max_position_pct": 0.03},
        "metadata": {"generation": generation, "status": status, "paper_trade_days": paper_days},
    })


def _make_score(sid: str, sharpe: float = 1.0, win_rate: float = 0.5, max_drawdown: float = 0.1) -> StrategyScore:
    return StrategyScore(
        strategy_id=sid,
        sharpe_ratio=sharpe,
        win_rate=win_rate,
        max_drawdown=max_drawdown,
    )


def _pool_with_n(n: int) -> StrategyPool:
    pool = StrategyPool()
    for i in range(n):
        sid = f"s{i}"
        s = _make_strategy(sid=sid, name=f"strat_{i}")
        pool.add(s)
        score = _make_score(sid=sid, sharpe=float(i), win_rate=0.5, max_drawdown=0.1)
        pool.update_score(sid, score)
    return pool


def _mock_db_pool() -> MagicMock:
    # / mock asyncpg pool
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    # / fetch for evolution_log max generation
    pool.fetch = AsyncMock(return_value=[{"max_gen": 5}])
    conn.fetchrow = AsyncMock(return_value={"id": 1})
    conn.fetch = AsyncMock(return_value=[])
    return pool


def _backtest_result(strategy_id: str, sharpe: float = 1.5, win_rate: float = 0.6, max_dd: float = 0.1, total_trades: int = 20, total_return: float = 5000.0):
    from src.strategies.backtest import BacktestResult
    return BacktestResult(
        strategy_id=strategy_id,
        strategy_name=f"test_{strategy_id}",
        sharpe_ratio=sharpe,
        win_rate=win_rate,
        max_drawdown_pct=max_dd,
        total_trades=total_trades,
        total_return=total_return,
    )


# ────────────────────────────────────────────────────────────────
# full loop
# ────────────────────────────────────────────────────────────────


class TestFullLoop:
    @pytest.mark.asyncio
    async def test_full_loop_with_8_strategies(self):
        # / 8 strategies: bottom 2 killed, 2 mutated, backtested, scored
        db_pool = _mock_db_pool()
        strategy_pool = _pool_with_n(8)
        engine = EvolutionEngine(rng=np.random.default_rng(42))

        # / mock fetch_strategy_scores to return scores matching pool
        scores = [
            {"strategy_id": f"s{i}", "sharpe_ratio": Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(8)
        ]

        # / mock the mutator to return valid configs
        mutated_config_template = {
            "id": "strategy_mutated",
            "name": "mutated",
            "version": 1,
            "asset_class": "stocks",
            "universe": "all_stocks",
            "entry_conditions": {
                "operator": "AND",
                "signals": [{"indicator": "rsi", "condition": "below", "threshold": 25, "period": 14}],
            },
            "exit_conditions": {"stop_loss": {"type": "fixed_pct", "pct": 0.04}},
            "position_sizing": {"method": "fixed_pct", "max_position_pct": 0.03},
            "metadata": {"generation": 3, "status": "backtest_pending"},
        }

        # / mock walk-forward to pass validation (oos sharpe > 0.3, degradation < 50%)
        wf_mock = MagicMock()
        wf_mock.avg_oos_sharpe = 1.5

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.fetch_recent_trades", new_callable=AsyncMock, return_value=[]), \
             patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock, return_value=1), \
             patch("src.evolution.evolution_engine.mutate_strategy", new_callable=AsyncMock, return_value=mutated_config_template), \
             patch("src.evolution.evolution_engine.run_backtest", new_callable=AsyncMock, return_value=_backtest_result("strategy_mutated", sharpe=2.0)), \
             patch("src.strategies.walk_forward.walk_forward_test", new_callable=AsyncMock, return_value=wf_mock), \
             patch("src.evolution.evolution_engine.save_config", return_value=None), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):

            result = await engine.run(db_pool, strategy_pool, market_data={"AAPL": MagicMock()})

        assert result["generation"] == 6  # / max_gen was 5, so 5+1=6
        assert len(result["killed"]) == 2  # / bottom quartile of 8 = 2
        assert len(result["mutated"]) > 0

    @pytest.mark.asyncio
    async def test_empty_pool_returns_early(self):
        db_pool = _mock_db_pool()
        strategy_pool = StrategyPool()
        engine = EvolutionEngine()

        result = await engine.run(db_pool, strategy_pool)

        assert result["generation"] == 0
        assert result["killed"] == []
        assert result["mutated"] == []
        assert result["promoted"] == []

    @pytest.mark.asyncio
    async def test_fewer_than_4_strategies_nothing_killed(self):
        db_pool = _mock_db_pool()
        strategy_pool = _pool_with_n(3)
        engine = EvolutionEngine()

        scores = [
            {"strategy_id": f"s{i}", "sharpe_ratio": Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(3)
        ]

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):
            result = await engine.run(db_pool, strategy_pool)

        assert result["killed"] == []
        assert result["mutated"] == []


# ────────────────────────────────────────────────────────────────
# mutation scoring
# ────────────────────────────────────────────────────────────────


class TestMutationScoring:
    @pytest.mark.asyncio
    async def test_mutation_above_median_added(self):
        # / composite > median -> paper_trading
        db_pool = _mock_db_pool()
        strategy_pool = _pool_with_n(8)
        engine = EvolutionEngine(rng=np.random.default_rng(42))

        scores = [
            {"strategy_id": f"s{i}", "sharpe_ratio": Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(8)
        ]

        mutated_config = {
            "id": "strategy_good",
            "name": "good_mutation",
            "version": 1,
            "asset_class": "stocks",
            "universe": "all_stocks",
            "entry_conditions": {
                "operator": "AND",
                "signals": [{"indicator": "rsi", "condition": "below", "threshold": 25, "period": 14}],
            },
            "exit_conditions": {"stop_loss": {"type": "fixed_pct", "pct": 0.04}},
            "position_sizing": {"method": "fixed_pct", "max_position_pct": 0.03},
            "metadata": {"generation": 3, "status": "backtest_pending"},
        }

        # / high sharpe -> above median composite
        bt_result = _backtest_result("strategy_good", sharpe=10.0, win_rate=0.8, max_dd=0.05)

        wf_mock = MagicMock()
        wf_mock.avg_oos_sharpe = 8.0

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.fetch_recent_trades", new_callable=AsyncMock, return_value=[]), \
             patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock, return_value=1), \
             patch("src.evolution.evolution_engine.mutate_strategy", new_callable=AsyncMock, return_value=mutated_config), \
             patch("src.evolution.evolution_engine.run_backtest", new_callable=AsyncMock, return_value=bt_result), \
             patch("src.strategies.walk_forward.walk_forward_test", new_callable=AsyncMock, return_value=wf_mock), \
             patch("src.evolution.evolution_engine.save_config", return_value=None), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):

            result = await engine.run(db_pool, strategy_pool, market_data={"AAPL": MagicMock()})

        # / check that mutation was added as paper_trading
        added = [m for m in result["mutated"] if m.get("status") == "paper_trading"]
        assert len(added) > 0

    @pytest.mark.asyncio
    async def test_mutation_below_median_not_added(self):
        # / composite < median -> discarded
        db_pool = _mock_db_pool()
        strategy_pool = _pool_with_n(8)
        engine = EvolutionEngine(rng=np.random.default_rng(42))

        scores = [
            {"strategy_id": f"s{i}", "sharpe_ratio": Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(8)
        ]

        mutated_config = {
            "id": "strategy_bad",
            "name": "bad_mutation",
            "version": 1,
            "asset_class": "stocks",
            "universe": "all_stocks",
            "entry_conditions": {
                "operator": "AND",
                "signals": [{"indicator": "rsi", "condition": "below", "threshold": 25, "period": 14}],
            },
            "exit_conditions": {"stop_loss": {"type": "fixed_pct", "pct": 0.04}},
            "position_sizing": {"method": "fixed_pct", "max_position_pct": 0.03},
            "metadata": {"generation": 3, "status": "backtest_pending"},
        }

        # / very low sharpe -> below median composite
        bt_result = _backtest_result("strategy_bad", sharpe=-5.0, win_rate=0.1, max_dd=0.5)

        # / mock walk-forward to pass so median check catches it
        wf_mock = MagicMock()
        wf_mock.avg_oos_sharpe = 0.5

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.fetch_recent_trades", new_callable=AsyncMock, return_value=[]), \
             patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock, return_value=1), \
             patch("src.evolution.evolution_engine.mutate_strategy", new_callable=AsyncMock, return_value=mutated_config), \
             patch("src.evolution.evolution_engine.run_backtest", new_callable=AsyncMock, return_value=bt_result), \
             patch("src.strategies.walk_forward.walk_forward_test", new_callable=AsyncMock, return_value=wf_mock), \
             patch("src.evolution.evolution_engine.save_config", return_value=None), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):

            result = await engine.run(db_pool, strategy_pool, market_data={"AAPL": MagicMock()})

        discarded = [m for m in result["mutated"] if m.get("status") == "discarded"]
        assert len(discarded) > 0


# ────────────────────────────────────────────────────────────────
# promotion
# ────────────────────────────────────────────────────────────────


class TestPromotion:
    @pytest.mark.asyncio
    async def test_promotion_after_14_days(self):
        # / paper strategy with good score promoted to live
        db_pool = _mock_db_pool()
        strategy_pool = StrategyPool()

        # / add 5 strategies; s0 is paper_trading with 20 days and high sharpe
        # / give s0 the highest sharpe so it doesn't get killed
        for i in range(5):
            status = "paper_trading" if i == 0 else "live"
            s = _make_strategy(f"s{i}", status=status)
            strategy_pool.add(s, status=status)
            # / backdate s0's status_changed_at to 20 days ago so dynamic paper_days >= 14
            if i == 0:
                strategy_pool.get(f"s{i}").status_changed_at = datetime.now(timezone.utc) - timedelta(days=20)
            # / s0 gets highest sharpe (10.0) so it won't be bottom quartile
            sharpe = 10.0 if i == 0 else float(i)
            score = _make_score(f"s{i}", sharpe=sharpe)
            strategy_pool.update_score(f"s{i}", score)

        scores = [
            {"strategy_id": f"s{i}",
             "sharpe_ratio": Decimal("10.0") if i == 0 else Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(5)
        ]

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.fetch_recent_trades", new_callable=AsyncMock, return_value=[]), \
             patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock, return_value=1), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):
            result = await engine_run_no_market(db_pool, strategy_pool)

        assert len(result["promoted"]) >= 1
        promoted_ids = [p["id"] for p in result["promoted"]]
        assert "s0" in promoted_ids

    @pytest.mark.asyncio
    async def test_promotion_bad_score_stays_paper(self):
        # / paper strategy with sharpe < 0.8 stays paper_trading
        db_pool = _mock_db_pool()
        strategy_pool = StrategyPool()

        # / s0 has low sharpe (0.3) but 20 paper days — won't meet promotion threshold
        # / give enough strategies and make s0 mid-ranked so it doesn't get killed
        for i in range(5):
            status = "paper_trading" if i == 0 else "live"
            s = _make_strategy(f"s{i}", status=status)
            strategy_pool.add(s, status=status)
            # / backdate s0's status_changed_at to 20 days ago
            if i == 0:
                strategy_pool.get(f"s{i}").status_changed_at = datetime.now(timezone.utc) - timedelta(days=20)
            # / s0 at 0.3, s1=1, s2=2, s3=3, s4=4 — s1 is bottom quartile, s0 is low but not bottom
            sharpe = 5.0 if i == 0 else float(i)
            score = _make_score(f"s{i}", sharpe=sharpe)
            strategy_pool.update_score(f"s{i}", score)

        # / now override s0's sharpe in the db scores to 0.3
        scores = [
            {"strategy_id": f"s{i}",
             "sharpe_ratio": Decimal("0.3") if i == 0 else Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(5)
        ]

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.fetch_recent_trades", new_callable=AsyncMock, return_value=[]), \
             patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock, return_value=1), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):
            result = await engine_run_no_market(db_pool, strategy_pool)

        promoted_ids = [p["id"] for p in result["promoted"]]
        assert "s0" not in promoted_ids
        # / s0 should still be paper_trading (not promoted to live)
        entry = strategy_pool.get("s0")
        if entry:
            assert entry.status != "live"


# ────────────────────────────────────────────────────────────────
# generation counter
# ────────────────────────────────────────────────────────────────


class TestGenerationCounter:
    @pytest.mark.asyncio
    async def test_generation_counter_increments(self):
        db_pool = _mock_db_pool()
        # / mock max generation = 10
        db_pool.fetch = AsyncMock(return_value=[{"max_gen": 10}])
        strategy_pool = _pool_with_n(4)
        engine = EvolutionEngine()

        scores = [
            {"strategy_id": f"s{i}", "sharpe_ratio": Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(4)
        ]

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.fetch_recent_trades", new_callable=AsyncMock, return_value=[]), \
             patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock, return_value=1), \
             patch("src.evolution.evolution_engine.mutate_strategy", new_callable=AsyncMock, return_value={"id": "x", "name": "x", "metadata": {"status": "backtest_pending", "generation": 1}}), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):
            result = await engine.run(db_pool, strategy_pool)

        assert result["generation"] == 11  # / 10 + 1

    @pytest.mark.asyncio
    async def test_generation_starts_at_1_when_no_history(self):
        db_pool = _mock_db_pool()
        db_pool.fetch = AsyncMock(return_value=[{"max_gen": 0}])
        strategy_pool = _pool_with_n(4)
        engine = EvolutionEngine()

        scores = [
            {"strategy_id": f"s{i}", "sharpe_ratio": Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(4)
        ]

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.fetch_recent_trades", new_callable=AsyncMock, return_value=[]), \
             patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock, return_value=1), \
             patch("src.evolution.evolution_engine.mutate_strategy", new_callable=AsyncMock, return_value={"id": "x", "name": "x", "metadata": {"status": "backtest_pending", "generation": 1}}), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):
            result = await engine.run(db_pool, strategy_pool)

        assert result["generation"] == 1


# ────────────────────────────────────────────────────────────────
# parallel backtesting
# ────────────────────────────────────────────────────────────────


class TestParallelBacktesting:
    @pytest.mark.asyncio
    async def test_parallel_backtesting(self):
        # / verify run_backtest is called for each mutation via asyncio.gather
        db_pool = _mock_db_pool()
        strategy_pool = _pool_with_n(8)
        engine = EvolutionEngine(rng=np.random.default_rng(42))

        scores = [
            {"strategy_id": f"s{i}", "sharpe_ratio": Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(8)
        ]

        mutated_config = {
            "id": "strategy_par",
            "name": "parallel_test",
            "version": 1,
            "asset_class": "stocks",
            "universe": "all_stocks",
            "entry_conditions": {
                "operator": "AND",
                "signals": [{"indicator": "rsi", "condition": "below", "threshold": 25, "period": 14}],
            },
            "exit_conditions": {"stop_loss": {"type": "fixed_pct", "pct": 0.04}},
            "position_sizing": {"method": "fixed_pct", "max_position_pct": 0.03},
            "metadata": {"generation": 3, "status": "backtest_pending"},
        }

        mock_run_backtest = AsyncMock(return_value=_backtest_result("strategy_par", sharpe=2.0))

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.fetch_recent_trades", new_callable=AsyncMock, return_value=[]), \
             patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock, return_value=1), \
             patch("src.evolution.evolution_engine.mutate_strategy", new_callable=AsyncMock, return_value=mutated_config), \
             patch("src.evolution.evolution_engine.run_backtest", mock_run_backtest), \
             patch("src.evolution.evolution_engine.save_config", return_value=None), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):

            result = await engine.run(db_pool, strategy_pool, market_data={"AAPL": MagicMock()})

        # / run_backtest called once per killed strategy (2 killed from 8)
        assert mock_run_backtest.call_count == 2


# ────────────────────────────────────────────────────────────────
# evolution log entries
# ────────────────────────────────────────────────────────────────


class TestEvolutionLog:
    @pytest.mark.asyncio
    async def test_evolution_log_entries_created(self):
        # / verify kill + mutate actions logged to db
        db_pool = _mock_db_pool()
        strategy_pool = _pool_with_n(8)
        engine = EvolutionEngine(rng=np.random.default_rng(42))

        scores = [
            {"strategy_id": f"s{i}", "sharpe_ratio": Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(8)
        ]

        mutated_config = {
            "id": "strategy_log_test",
            "name": "log_test",
            "version": 1,
            "asset_class": "stocks",
            "universe": "all_stocks",
            "entry_conditions": {
                "operator": "AND",
                "signals": [{"indicator": "rsi", "condition": "below", "threshold": 25, "period": 14}],
            },
            "exit_conditions": {"stop_loss": {"type": "fixed_pct", "pct": 0.04}},
            "position_sizing": {"method": "fixed_pct", "max_position_pct": 0.03},
            "metadata": {"generation": 3, "status": "backtest_pending"},
        }

        mock_store_log = AsyncMock(return_value=1)

        wf_mock = MagicMock()
        wf_mock.avg_oos_sharpe = 4.0

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.fetch_recent_trades", new_callable=AsyncMock, return_value=[]), \
             patch("src.evolution.evolution_engine.store_evolution_log", mock_store_log), \
             patch("src.evolution.evolution_engine.mutate_strategy", new_callable=AsyncMock, return_value=mutated_config), \
             patch("src.evolution.evolution_engine.run_backtest", new_callable=AsyncMock, return_value=_backtest_result("strategy_log_test", sharpe=5.0)), \
             patch("src.strategies.walk_forward.walk_forward_test", new_callable=AsyncMock, return_value=wf_mock), \
             patch("src.evolution.evolution_engine.save_config", return_value=None), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):

            result = await engine.run(db_pool, strategy_pool, market_data={"AAPL": MagicMock()})

        # / should have kill logs (2 for bottom quartile) + mutate logs
        # / store_evolution_log(pool, generation, action, ...) — action is args[2]
        call_actions = [call.args[2] for call in mock_store_log.call_args_list]
        assert "kill" in call_actions
        kill_count = call_actions.count("kill")
        assert kill_count == 2  # / bottom quartile of 8


# ────────────────────────────────────────────────────────────────
# no market data
# ────────────────────────────────────────────────────────────────


class TestNoMarketData:
    @pytest.mark.asyncio
    async def test_no_market_data_skips_backtest(self):
        db_pool = _mock_db_pool()
        strategy_pool = _pool_with_n(8)
        engine = EvolutionEngine(rng=np.random.default_rng(42))

        scores = [
            {"strategy_id": f"s{i}", "sharpe_ratio": Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(8)
        ]

        mutated_config = {
            "id": "strategy_nobt",
            "name": "no_backtest",
            "version": 1,
            "asset_class": "stocks",
            "universe": "all_stocks",
            "entry_conditions": {
                "operator": "AND",
                "signals": [{"indicator": "rsi", "condition": "below", "threshold": 25, "period": 14}],
            },
            "exit_conditions": {"stop_loss": {"type": "fixed_pct", "pct": 0.04}},
            "position_sizing": {"method": "fixed_pct", "max_position_pct": 0.03},
            "metadata": {"generation": 3, "status": "backtest_pending"},
        }

        mock_run_bt = AsyncMock()

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.fetch_recent_trades", new_callable=AsyncMock, return_value=[]), \
             patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock, return_value=1), \
             patch("src.evolution.evolution_engine.mutate_strategy", new_callable=AsyncMock, return_value=mutated_config), \
             patch("src.evolution.evolution_engine.run_backtest", mock_run_bt), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):

            # / no market_data passed
            result = await engine.run(db_pool, strategy_pool, market_data=None)

        # / run_backtest should not be called
        mock_run_bt.assert_not_called()
        assert result["mutated"] == []

    @pytest.mark.asyncio
    async def test_scores_update_pool(self):
        # / verify db scores update strategy pool entries
        db_pool = _mock_db_pool()
        strategy_pool = StrategyPool()
        for i in range(4):
            s = _make_strategy(f"s{i}")
            strategy_pool.add(s)

        # / no scores initially
        for i in range(4):
            assert strategy_pool.get(f"s{i}").score is None

        scores = [
            {"strategy_id": f"s{i}", "sharpe_ratio": Decimal(str(float(i + 1))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(4)
        ]

        engine = EvolutionEngine()

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):
            await engine.run(db_pool, strategy_pool)

        # / now all should have scores
        for i in range(4):
            entry = strategy_pool.get(f"s{i}")
            if entry:
                assert entry.score is not None
                assert entry.score.sharpe_ratio == float(i + 1)


# ────────────────────────────────────────────────────────────────
# error handling
# ────────────────────────────────────────────────────────────────


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_mutation_exception_captured(self):
        db_pool = _mock_db_pool()
        strategy_pool = _pool_with_n(8)
        engine = EvolutionEngine(rng=np.random.default_rng(42))

        scores = [
            {"strategy_id": f"s{i}", "sharpe_ratio": Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(8)
        ]

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.fetch_recent_trades", new_callable=AsyncMock, return_value=[]), \
             patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock, return_value=1), \
             patch("src.evolution.evolution_engine.mutate_strategy", new_callable=AsyncMock, side_effect=RuntimeError("mutation boom")), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):

            result = await engine.run(db_pool, strategy_pool, market_data={"AAPL": MagicMock()})

        # / errors captured, not raised
        assert len(result["errors"]) > 0
        assert "mutation boom" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_backtest_exception_captured(self):
        db_pool = _mock_db_pool()
        strategy_pool = _pool_with_n(8)
        engine = EvolutionEngine(rng=np.random.default_rng(42))

        scores = [
            {"strategy_id": f"s{i}", "sharpe_ratio": Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(8)
        ]

        mutated_config = {
            "id": "strategy_bt_err",
            "name": "bt_error",
            "version": 1,
            "asset_class": "stocks",
            "universe": "all_stocks",
            "entry_conditions": {
                "operator": "AND",
                "signals": [{"indicator": "rsi", "condition": "below", "threshold": 25, "period": 14}],
            },
            "exit_conditions": {"stop_loss": {"type": "fixed_pct", "pct": 0.04}},
            "position_sizing": {"method": "fixed_pct", "max_position_pct": 0.03},
            "metadata": {"generation": 3, "status": "backtest_pending"},
        }

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.fetch_recent_trades", new_callable=AsyncMock, return_value=[]), \
             patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock, return_value=1), \
             patch("src.evolution.evolution_engine.mutate_strategy", new_callable=AsyncMock, return_value=mutated_config), \
             patch("src.evolution.evolution_engine.run_backtest", new_callable=AsyncMock, side_effect=RuntimeError("backtest boom")), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):

            result = await engine.run(db_pool, strategy_pool, market_data={"AAPL": MagicMock()})

        assert any("backtest boom" in e for e in result["errors"])


# ────────────────────────────────────────────────────────────────
# dynamic paper_trade_days
# ────────────────────────────────────────────────────────────────


class TestDynamicPaperDays:
    @pytest.mark.asyncio
    async def test_paper_days_computed_from_status_changed_at(self):
        # / paper_days should be computed dynamically, not read from config
        db_pool = _mock_db_pool()
        strategy_pool = StrategyPool()

        for i in range(5):
            status = "paper_trading" if i == 0 else "live"
            # / config has paper_trade_days=0 — should NOT block promotion
            s = _make_strategy(f"s{i}", paper_days=0, status=status)
            strategy_pool.add(s, status=status)
            if i == 0:
                # / set status_changed_at to 15 days ago — should qualify
                strategy_pool.get(f"s{i}").status_changed_at = datetime.now(timezone.utc) - timedelta(days=15)
            sharpe = 10.0 if i == 0 else float(i)
            score = _make_score(f"s{i}", sharpe=sharpe)
            strategy_pool.update_score(f"s{i}", score)

        scores = [
            {"strategy_id": f"s{i}",
             "sharpe_ratio": Decimal("10.0") if i == 0 else Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(5)
        ]

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.fetch_recent_trades", new_callable=AsyncMock, return_value=[]), \
             patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock, return_value=1), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):
            result = await engine_run_no_market(db_pool, strategy_pool)

        promoted_ids = [p["id"] for p in result["promoted"]]
        assert "s0" in promoted_ids

    @pytest.mark.asyncio
    async def test_recent_paper_strategy_not_promoted(self):
        # / strategy added recently (< 14 days) should not promote
        db_pool = _mock_db_pool()
        strategy_pool = StrategyPool()

        for i in range(5):
            status = "paper_trading" if i == 0 else "live"
            s = _make_strategy(f"s{i}", status=status)
            strategy_pool.add(s, status=status)
            # / s0 status_changed_at defaults to now — only ~0 days
            sharpe = 10.0 if i == 0 else float(i)
            score = _make_score(f"s{i}", sharpe=sharpe)
            strategy_pool.update_score(f"s{i}", score)

        scores = [
            {"strategy_id": f"s{i}",
             "sharpe_ratio": Decimal("10.0") if i == 0 else Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(5)
        ]

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.fetch_recent_trades", new_callable=AsyncMock, return_value=[]), \
             patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock, return_value=1), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):
            result = await engine_run_no_market(db_pool, strategy_pool)

        promoted_ids = [p["id"] for p in result["promoted"]]
        assert "s0" not in promoted_ids


# ────────────────────────────────────────────────────────────────
# cycle_complete log
# ────────────────────────────────────────────────────────────────


class TestCycleCompleteLog:
    @pytest.mark.asyncio
    async def test_cycle_complete_logged_every_run(self):
        # / every evolution run should write a cycle_complete log entry
        db_pool = _mock_db_pool()
        strategy_pool = _pool_with_n(4)
        engine = EvolutionEngine()

        scores = [
            {"strategy_id": f"s{i}", "sharpe_ratio": Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(4)
        ]

        mock_store_log = AsyncMock(return_value=1)

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.fetch_recent_trades", new_callable=AsyncMock, return_value=[]), \
             patch("src.evolution.evolution_engine.store_evolution_log", mock_store_log), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):
            await engine.run(db_pool, strategy_pool)

        call_actions = [call.args[2] for call in mock_store_log.call_args_list]
        assert "cycle_complete" in call_actions

    @pytest.mark.asyncio
    async def test_cycle_complete_logged_even_with_zero_actions(self):
        # / when no kills/mutates/promotions happen, still get a log entry
        db_pool = _mock_db_pool()
        strategy_pool = _pool_with_n(3)  # / < 4 strategies: nothing killed
        engine = EvolutionEngine()

        scores = [
            {"strategy_id": f"s{i}", "sharpe_ratio": Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(3)
        ]

        mock_store_log = AsyncMock(return_value=1)

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.store_evolution_log", mock_store_log), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):
            result = await engine.run(db_pool, strategy_pool)

        # / zero actions taken
        assert result["killed"] == []
        assert result["mutated"] == []
        assert result["promoted"] == []

        # / but cycle_complete still logged
        call_actions = [call.args[2] for call in mock_store_log.call_args_list]
        assert "cycle_complete" in call_actions

    @pytest.mark.asyncio
    async def test_cycle_complete_contains_counts(self):
        # / the cycle_complete reason string should contain action counts
        db_pool = _mock_db_pool()
        strategy_pool = _pool_with_n(8)
        engine = EvolutionEngine(rng=np.random.default_rng(42))

        scores = [
            {"strategy_id": f"s{i}", "sharpe_ratio": Decimal(str(float(i))),
             "max_drawdown": Decimal("0.1"), "win_rate": Decimal("0.5"),
             "total_trades": 10, "brier_score": None}
            for i in range(8)
        ]

        mutated_config = {
            "id": "strategy_cc",
            "name": "cc_test",
            "version": 1,
            "asset_class": "stocks",
            "universe": "all_stocks",
            "entry_conditions": {
                "operator": "AND",
                "signals": [{"indicator": "rsi", "condition": "below", "threshold": 25, "period": 14}],
            },
            "exit_conditions": {"stop_loss": {"type": "fixed_pct", "pct": 0.04}},
            "position_sizing": {"method": "fixed_pct", "max_position_pct": 0.03},
            "metadata": {"generation": 3, "status": "backtest_pending"},
        }

        mock_store_log = AsyncMock(return_value=1)

        wf_mock = MagicMock()
        wf_mock.avg_oos_sharpe = 4.0

        with patch("src.evolution.evolution_engine.fetch_strategy_scores", new_callable=AsyncMock, return_value=scores), \
             patch("src.evolution.evolution_engine.fetch_recent_trades", new_callable=AsyncMock, return_value=[]), \
             patch("src.evolution.evolution_engine.store_evolution_log", mock_store_log), \
             patch("src.evolution.evolution_engine.mutate_strategy", new_callable=AsyncMock, return_value=mutated_config), \
             patch("src.evolution.evolution_engine.run_backtest", new_callable=AsyncMock, return_value=_backtest_result("strategy_cc", sharpe=5.0)), \
             patch("src.strategies.walk_forward.walk_forward_test", new_callable=AsyncMock, return_value=wf_mock), \
             patch("src.evolution.evolution_engine.save_config", return_value=None), \
             patch("src.evolution.evolution_engine.generate_report", new_callable=AsyncMock, return_value="# report"), \
             patch("src.evolution.evolution_engine.update_docs", new_callable=AsyncMock):

            result = await engine.run(db_pool, strategy_pool, market_data={"AAPL": MagicMock()})

        # / find the cycle_complete call and check its reason string
        cycle_calls = [call for call in mock_store_log.call_args_list if call.args[2] == "cycle_complete"]
        assert len(cycle_calls) == 1
        reason = cycle_calls[0].args[5]
        assert "2 killed" in reason  # / bottom quartile of 8 = 2
        assert "promoted" in reason


# ────────────────────────────────────────────────────────────────
# / kill persistence (bug 1b: killed strategies must survive restart)
# ────────────────────────────────────────────────────────────────


class TestKillPersistence:
    @pytest.mark.asyncio
    async def test_kill_writes_metadata_status_and_save_config(self):
        # / bug 1b: _kill_bottom_quartile must stamp metadata.status = "killed"
        # / AND call save_config, otherwise orchestrator reloads killed strategies
        strategy_pool = StrategyPool()
        for i in range(4):
            s = _make_strategy(sid=f"s{i}", name=f"strat_{i}")
            strategy_pool.add(s)
            strategy_pool.update_score(
                f"s{i}",
                StrategyScore(
                    strategy_id=f"s{i}",
                    sharpe_ratio=float(i),
                    win_rate=0.5,
                    max_drawdown=0.1,
                    total_trades=10,
                ),
            )

        engine = EvolutionEngine(rng=np.random.default_rng(42))
        save_mock = MagicMock()

        with patch("src.evolution.evolution_engine.save_config", save_mock), \
             patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock):
            killed = await engine._kill_bottom_quartile(
                _mock_db_pool(), 1, strategy_pool, {"killed": []},
            )

        # / bottom quartile of 4 = 1 strategy killed
        assert len(killed) == 1
        killed_sid = killed[0]["id"]
        # / pool must report killed
        entry = strategy_pool.get(killed_sid)
        assert entry.status == "killed"
        # / config's metadata.status must also be "killed" so loader skips on restart
        assert killed[0]["config"]["metadata"]["status"] == "killed"
        # / save_config must have been called with the killed config
        save_mock.assert_called_once()
        saved_config = save_mock.call_args.args[0]
        assert saved_config["metadata"]["status"] == "killed"

    @pytest.mark.asyncio
    async def test_save_config_failure_does_not_abort_kill(self):
        # / persistence is best-effort — if disk write fails, in-memory state still kills
        strategy_pool = StrategyPool()
        for i in range(4):
            s = _make_strategy(sid=f"s{i}")
            strategy_pool.add(s)
            strategy_pool.update_score(
                f"s{i}",
                StrategyScore(
                    strategy_id=f"s{i}",
                    sharpe_ratio=float(i),
                    win_rate=0.5,
                    max_drawdown=0.1,
                    total_trades=10,
                ),
            )

        engine = EvolutionEngine(rng=np.random.default_rng(42))

        with patch("src.evolution.evolution_engine.save_config", side_effect=OSError("disk full")), \
             patch("src.evolution.evolution_engine.store_evolution_log", new_callable=AsyncMock):
            killed = await engine._kill_bottom_quartile(
                _mock_db_pool(), 1, strategy_pool, {"killed": []},
            )

        assert len(killed) == 1
        assert strategy_pool.get(killed[0]["id"]).status == "killed"


# ────────────────────────────────────────────────────────────────
# helper
# ────────────────────────────────────────────────────────────────

async def engine_run_no_market(db_pool: MagicMock, strategy_pool: StrategyPool) -> dict:
    # / run engine without market data (no backtest phase)
    engine = EvolutionEngine(rng=np.random.default_rng(42))
    return await engine.run(db_pool, strategy_pool, market_data=None)

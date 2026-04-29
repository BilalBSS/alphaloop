# / tests for strategy pool

from __future__ import annotations

import pytest

from src.strategies.base_strategy import ConfigDrivenStrategy
from src.strategies.strategy_pool import (
    StrategyEntry,
    StrategyPool,
    StrategyScore,
    compute_composite_score,
)


def _make_strategy(sid: str = "s1", name: str = "test_strat") -> ConfigDrivenStrategy:
    # / minimal valid config for ConfigDrivenStrategy
    return ConfigDrivenStrategy({"id": sid, "name": name})


def _make_score(
    sid: str = "s1",
    sharpe: float = 1.0,
    win_rate: float = 0.5,
    max_drawdown: float = 0.1,
    brier: float | None = None,
    total_pnl: float = 0.0,
    total_trades: int = 0,
) -> StrategyScore:
    return StrategyScore(
        strategy_id=sid,
        sharpe_ratio=sharpe,
        win_rate=win_rate,
        max_drawdown=max_drawdown,
        brier_score=brier,
        total_pnl=total_pnl,
        total_trades=total_trades,
    )


def _pool_with_n(n: int, scored: bool = True) -> StrategyPool:
    # / helper to build a pool with n strategies, optionally scored
    pool = StrategyPool()
    for i in range(n):
        sid = f"s{i}"
        s = _make_strategy(sid=sid, name=f"strat_{i}")
        pool.add(s)
        if scored:
            # / spread scores so ranking is deterministic: s0 worst, s(n-1) best
            score = _make_score(sid=sid, sharpe=float(i), win_rate=0.5, max_drawdown=0.1)
            pool.update_score(sid, score)
    return pool


# ────────────────────────────────────────────────────────────────
# compute_composite_score
# ────────────────────────────────────────────────────────────────

class TestCompositeScore:
    def test_basic_formula(self):
        # / sharpe * 0.4 + win_rate * 0.3 - abs(max_drawdown) * 0.2
        result = compute_composite_score(sharpe=2.0, win_rate=0.6, max_drawdown=-0.15)
        expected = 2.0 * 0.4 + 0.6 * 0.3 - 0.15 * 0.2
        assert result == pytest.approx(expected)

    def test_with_brier(self):
        # / adds (0.25 - brier) * 0.1
        result = compute_composite_score(sharpe=1.0, win_rate=0.5, max_drawdown=0.1, brier=0.20)
        expected = 1.0 * 0.4 + 0.5 * 0.3 - 0.1 * 0.2 + (0.25 - 0.20) * 0.1
        assert result == pytest.approx(expected)

    def test_brier_none_excluded(self):
        # / no brier term when None
        with_brier = compute_composite_score(sharpe=1.0, win_rate=0.5, max_drawdown=0.1, brier=0.25)
        without = compute_composite_score(sharpe=1.0, win_rate=0.5, max_drawdown=0.1, brier=None)
        # / brier=0.25 contributes (0.25-0.25)*0.1 = 0, so they differ by exactly 0 here
        # / use a brier != 0.25 to see the actual difference
        with_brier2 = compute_composite_score(sharpe=1.0, win_rate=0.5, max_drawdown=0.1, brier=0.10)
        assert with_brier2 > without

    def test_negative_drawdown_abs(self):
        # / drawdown can be passed as negative, abs() handles it
        pos = compute_composite_score(sharpe=1.0, win_rate=0.5, max_drawdown=0.1)
        neg = compute_composite_score(sharpe=1.0, win_rate=0.5, max_drawdown=-0.1)
        assert pos == pytest.approx(neg)

    def test_zero_inputs(self):
        result = compute_composite_score(sharpe=0.0, win_rate=0.0, max_drawdown=0.0)
        assert result == pytest.approx(0.0)

    def test_high_brier_penalizes(self):
        # / brier > 0.25 produces negative brier contribution
        result = compute_composite_score(sharpe=0.0, win_rate=0.0, max_drawdown=0.0, brier=0.50)
        assert result < 0.0


# ────────────────────────────────────────────────────────────────
# StrategyPool.add
# ────────────────────────────────────────────────────────────────

class TestAdd:
    def test_add_single(self):
        pool = StrategyPool()
        s = _make_strategy("s1")
        pool.add(s)
        assert pool.size == 1
        assert pool.get("s1") is not None

    def test_add_multiple(self):
        pool = StrategyPool()
        pool.add(_make_strategy("a"))
        pool.add(_make_strategy("b"))
        pool.add(_make_strategy("c"))
        assert pool.size == 3

    def test_add_duplicate_ignored(self):
        pool = StrategyPool()
        s = _make_strategy("dup")
        pool.add(s)
        pool.add(s)
        assert pool.size == 1

    def test_default_status(self):
        pool = StrategyPool()
        pool.add(_make_strategy("s1"))
        entry = pool.get("s1")
        assert entry.status == "backtest_pending"

    def test_custom_status(self):
        pool = StrategyPool()
        pool.add(_make_strategy("s1"), status="live")
        entry = pool.get("s1")
        assert entry.status == "live"

    def test_entry_has_timestamps(self):
        pool = StrategyPool()
        pool.add(_make_strategy("s1"))
        entry = pool.get("s1")
        assert entry.added_at is not None
        assert entry.status_changed_at is not None

    def test_entry_score_initially_none(self):
        pool = StrategyPool()
        pool.add(_make_strategy("s1"))
        assert pool.get("s1").score is None


# ────────────────────────────────────────────────────────────────
# StrategyPool.remove
# ────────────────────────────────────────────────────────────────

class TestRemove:
    def test_remove_existing(self):
        pool = StrategyPool()
        pool.add(_make_strategy("s1"))
        result = pool.remove("s1")
        assert result is True
        assert pool.size == 0

    def test_remove_nonexisting(self):
        pool = StrategyPool()
        result = pool.remove("ghost")
        assert result is False

    def test_remove_idempotent(self):
        pool = StrategyPool()
        pool.add(_make_strategy("s1"))
        pool.remove("s1")
        result = pool.remove("s1")
        assert result is False

    def test_remove_doesnt_affect_others(self):
        pool = StrategyPool()
        pool.add(_make_strategy("a"))
        pool.add(_make_strategy("b"))
        pool.remove("a")
        assert pool.size == 1
        assert pool.get("b") is not None


# ────────────────────────────────────────────────────────────────
# StrategyPool.update_status
# ────────────────────────────────────────────────────────────────

class TestUpdateStatus:
    def test_valid_transition(self):
        pool = StrategyPool()
        pool.add(_make_strategy("s1"))
        result = pool.update_status("s1", "backtesting")
        assert result is True
        assert pool.get("s1").status == "backtesting"

    def test_all_valid_statuses(self):
        valid = ("backtest_pending", "backtesting", "paper_trading", "live", "killed")
        for status in valid:
            pool = StrategyPool()
            pool.add(_make_strategy("s1"))
            result = pool.update_status("s1", status)
            assert result is True
            assert pool.get("s1").status == status

    def test_invalid_status_raises(self):
        pool = StrategyPool()
        pool.add(_make_strategy("s1"))
        with pytest.raises(ValueError, match="invalid status"):
            pool.update_status("s1", "invalid_status")

    def test_nonexisting_strategy(self):
        pool = StrategyPool()
        result = pool.update_status("ghost", "live")
        assert result is False

    def test_updates_status_changed_at(self):
        pool = StrategyPool()
        pool.add(_make_strategy("s1"))
        original_ts = pool.get("s1").status_changed_at
        pool.update_status("s1", "backtesting")
        assert pool.get("s1").status_changed_at >= original_ts


# ────────────────────────────────────────────────────────────────
# StrategyPool.update_score
# ────────────────────────────────────────────────────────────────

class TestUpdateScore:
    def test_updates_score(self):
        pool = StrategyPool()
        pool.add(_make_strategy("s1"))
        score = _make_score("s1", sharpe=1.5, win_rate=0.6, max_drawdown=0.1)
        result = pool.update_score("s1", score)
        assert result is True
        assert pool.get("s1").score is not None

    def test_computes_composite(self):
        pool = StrategyPool()
        pool.add(_make_strategy("s1"))
        score = _make_score("s1", sharpe=2.0, win_rate=0.6, max_drawdown=0.15)
        pool.update_score("s1", score)
        expected = compute_composite_score(2.0, 0.6, 0.15, brier=None)
        assert pool.get("s1").score.composite_score == pytest.approx(expected)

    def test_composite_includes_brier(self):
        pool = StrategyPool()
        pool.add(_make_strategy("s1"))
        score = _make_score("s1", sharpe=1.0, win_rate=0.5, max_drawdown=0.1, brier=0.15)
        pool.update_score("s1", score)
        expected = compute_composite_score(1.0, 0.5, 0.1, brier=0.15)
        assert pool.get("s1").score.composite_score == pytest.approx(expected)

    def test_nonexisting_strategy(self):
        pool = StrategyPool()
        score = _make_score("ghost")
        result = pool.update_score("ghost", score)
        assert result is False

    def test_overwrite_previous_score(self):
        pool = StrategyPool()
        pool.add(_make_strategy("s1"))
        pool.update_score("s1", _make_score("s1", sharpe=1.0))
        pool.update_score("s1", _make_score("s1", sharpe=3.0))
        assert pool.get("s1").score.sharpe_ratio == 3.0


# ────────────────────────────────────────────────────────────────
# StrategyPool.get / get_strategy
# ────────────────────────────────────────────────────────────────

class TestGet:
    def test_get_returns_entry(self):
        pool = StrategyPool()
        pool.add(_make_strategy("s1"))
        entry = pool.get("s1")
        assert isinstance(entry, StrategyEntry)
        assert entry.strategy.strategy_id == "s1"

    def test_get_missing_returns_none(self):
        pool = StrategyPool()
        assert pool.get("nope") is None

    def test_get_strategy_returns_config_driven(self):
        pool = StrategyPool()
        s = _make_strategy("s1", name="my_strat")
        pool.add(s)
        result = pool.get_strategy("s1")
        assert isinstance(result, ConfigDrivenStrategy)
        assert result.name == "my_strat"

    def test_get_strategy_missing_returns_none(self):
        pool = StrategyPool()
        assert pool.get_strategy("nope") is None


# ────────────────────────────────────────────────────────────────
# StrategyPool.list_by_status
# ────────────────────────────────────────────────────────────────

class TestListByStatus:
    def test_filters_correctly(self):
        pool = StrategyPool()
        pool.add(_make_strategy("a"))
        pool.add(_make_strategy("b"), status="live")
        pool.add(_make_strategy("c"), status="live")
        pool.add(_make_strategy("d"), status="killed")
        live = pool.list_by_status("live")
        assert len(live) == 2
        ids = {e.strategy.strategy_id for e in live}
        assert ids == {"b", "c"}

    def test_empty_result(self):
        pool = StrategyPool()
        pool.add(_make_strategy("a"))
        assert pool.list_by_status("live") == []

    def test_empty_pool(self):
        pool = StrategyPool()
        assert pool.list_by_status("live") == []


# ────────────────────────────────────────────────────────────────
# StrategyPool.ranked
# ────────────────────────────────────────────────────────────────

class TestRanked:
    def test_descending_order(self):
        pool = _pool_with_n(5)
        ranked = pool.ranked()
        scores = [e.score.composite_score for e in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_filter_by_status(self):
        pool = StrategyPool()
        pool.add(_make_strategy("a"), status="live")
        pool.add(_make_strategy("b"), status="live")
        pool.add(_make_strategy("c"), status="killed")
        pool.update_score("a", _make_score("a", sharpe=2.0))
        pool.update_score("b", _make_score("b", sharpe=1.0))
        pool.update_score("c", _make_score("c", sharpe=3.0))
        ranked = pool.ranked(status="live")
        assert len(ranked) == 2
        assert ranked[0].strategy.strategy_id == "a"

    def test_unscored_strategies_last(self):
        pool = StrategyPool()
        pool.add(_make_strategy("scored"))
        pool.add(_make_strategy("unscored"))
        pool.update_score("scored", _make_score("scored", sharpe=0.5))
        ranked = pool.ranked()
        assert ranked[0].strategy.strategy_id == "scored"
        assert ranked[1].strategy.strategy_id == "unscored"

    def test_empty_pool(self):
        pool = StrategyPool()
        assert pool.ranked() == []

    def test_all_unscored(self):
        pool = StrategyPool()
        pool.add(_make_strategy("a"))
        pool.add(_make_strategy("b"))
        ranked = pool.ranked()
        assert len(ranked) == 2


# ────────────────────────────────────────────────────────────────
# StrategyPool.bottom_quartile
# ────────────────────────────────────────────────────────────────

class TestBottomQuartile:
    def test_returns_bottom_25_pct(self):
        pool = _pool_with_n(8)
        bottom = pool.bottom_quartile()
        # / 8 // 4 = 2 strategies
        assert len(bottom) == 2
        # / should be the two lowest scored: s0, s1
        ids = {e.strategy.strategy_id for e in bottom}
        assert ids == {"s0", "s1"}

    def test_needs_four_minimum(self):
        pool = _pool_with_n(3)
        assert pool.bottom_quartile() == []

    def test_exactly_four(self):
        pool = _pool_with_n(4)
        bottom = pool.bottom_quartile()
        # / 4 // 4 = 1, max(1, 1) = 1
        assert len(bottom) == 1
        assert bottom[0].strategy.strategy_id == "s0"

    def test_filters_by_status(self):
        pool = StrategyPool()
        for i in range(8):
            sid = f"s{i}"
            status = "live" if i < 6 else "killed"
            pool.add(_make_strategy(sid), status=status)
            pool.update_score(sid, _make_score(sid, sharpe=float(i)))
        bottom = pool.bottom_quartile(status="live")
        # / 6 live // 4 = 1
        assert len(bottom) == 1
        assert bottom[0].strategy.strategy_id == "s0"

    def test_empty_pool(self):
        pool = StrategyPool()
        assert pool.bottom_quartile() == []


# ────────────────────────────────────────────────────────────────
# StrategyPool.top_performers
# ────────────────────────────────────────────────────────────────

class TestTopPerformers:
    def test_returns_top_n(self):
        pool = _pool_with_n(10)
        top = pool.top_performers(n=3)
        assert len(top) == 3
        ids = [e.strategy.strategy_id for e in top]
        assert ids == ["s9", "s8", "s7"]

    def test_default_n_is_5(self):
        pool = _pool_with_n(10)
        top = pool.top_performers()
        assert len(top) == 5

    def test_fewer_than_n(self):
        pool = _pool_with_n(2)
        top = pool.top_performers(n=5)
        assert len(top) == 2

    def test_filter_by_status(self):
        pool = StrategyPool()
        pool.add(_make_strategy("a"), status="live")
        pool.add(_make_strategy("b"), status="paper_trading")
        pool.update_score("a", _make_score("a", sharpe=1.0))
        pool.update_score("b", _make_score("b", sharpe=5.0))
        top = pool.top_performers(n=5, status="live")
        assert len(top) == 1
        assert top[0].strategy.strategy_id == "a"

    def test_empty_pool(self):
        pool = StrategyPool()
        assert pool.top_performers() == []


# ────────────────────────────────────────────────────────────────
# StrategyPool.size / active_count
# ────────────────────────────────────────────────────────────────

class TestSizeAndActiveCount:
    def test_size_empty(self):
        pool = StrategyPool()
        assert pool.size == 0

    def test_size_tracks_adds(self):
        pool = StrategyPool()
        pool.add(_make_strategy("a"))
        pool.add(_make_strategy("b"))
        assert pool.size == 2

    def test_size_tracks_removes(self):
        pool = StrategyPool()
        pool.add(_make_strategy("a"))
        pool.add(_make_strategy("b"))
        pool.remove("a")
        assert pool.size == 1

    def test_active_count_empty(self):
        pool = StrategyPool()
        assert pool.active_count == 0

    def test_active_counts_live_and_paper(self):
        pool = StrategyPool()
        pool.add(_make_strategy("a"), status="live")
        pool.add(_make_strategy("b"), status="paper_trading")
        pool.add(_make_strategy("c"), status="backtesting")
        pool.add(_make_strategy("d"), status="killed")
        pool.add(_make_strategy("e"), status="backtest_pending")
        assert pool.active_count == 2

    def test_active_count_after_status_change(self):
        pool = StrategyPool()
        pool.add(_make_strategy("a"))
        assert pool.active_count == 0
        pool.update_status("a", "live")
        assert pool.active_count == 1
        pool.update_status("a", "killed")
        assert pool.active_count == 0


# ────────────────────────────────────────────────────────────────
# StrategyPool.summary
# ────────────────────────────────────────────────────────────────

class TestSummary:
    def test_keys_present(self):
        pool = _pool_with_n(3)
        s = pool.summary()
        assert "total" in s
        assert "active" in s
        assert "by_status" in s
        assert "top_3" in s

    def test_total_count(self):
        pool = _pool_with_n(5)
        assert pool.summary()["total"] == 5

    def test_active_count_in_summary(self):
        pool = StrategyPool()
        pool.add(_make_strategy("a"), status="live")
        pool.add(_make_strategy("b"), status="paper_trading")
        pool.add(_make_strategy("c"), status="killed")
        assert pool.summary()["active"] == 2

    def test_by_status_breakdown(self):
        pool = StrategyPool()
        pool.add(_make_strategy("a"), status="live")
        pool.add(_make_strategy("b"), status="live")
        pool.add(_make_strategy("c"), status="killed")
        by_status = pool.summary()["by_status"]
        assert by_status["live"] == 2
        assert by_status["killed"] == 1

    def test_top_3_entries(self):
        pool = _pool_with_n(5)
        top_3 = pool.summary()["top_3"]
        assert len(top_3) == 3
        # / each entry has id and score
        assert "id" in top_3[0]
        assert "score" in top_3[0]

    def test_top_3_score_none_when_unscored(self):
        pool = StrategyPool()
        pool.add(_make_strategy("a"))
        top_3 = pool.summary()["top_3"]
        assert len(top_3) == 1
        assert top_3[0]["score"] is None

    def test_empty_pool_summary(self):
        pool = StrategyPool()
        s = pool.summary()
        assert s["total"] == 0
        assert s["active"] == 0
        assert s["by_status"] == {}
        assert s["top_3"] == []


# ────────────────────────────────────────────────────────────────
# edge cases
# ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_single_strategy_ranked(self):
        pool = StrategyPool()
        pool.add(_make_strategy("only"))
        pool.update_score("only", _make_score("only", sharpe=1.0))
        ranked = pool.ranked()
        assert len(ranked) == 1

    def test_single_strategy_no_bottom_quartile(self):
        pool = _pool_with_n(1)
        assert pool.bottom_quartile() == []

    def test_all_same_scores_ranked(self):
        pool = StrategyPool()
        for i in range(5):
            sid = f"s{i}"
            pool.add(_make_strategy(sid))
            pool.update_score(sid, _make_score(sid, sharpe=1.0, win_rate=0.5, max_drawdown=0.1))
        ranked = pool.ranked()
        # / all composites equal — still returns all 5
        assert len(ranked) == 5
        scores = [e.score.composite_score for e in ranked]
        assert len(set(scores)) == 1

    def test_all_same_scores_bottom_quartile(self):
        pool = StrategyPool()
        for i in range(8):
            sid = f"s{i}"
            pool.add(_make_strategy(sid))
            pool.update_score(sid, _make_score(sid, sharpe=1.0, win_rate=0.5, max_drawdown=0.1))
        bottom = pool.bottom_quartile()
        # / 8 // 4 = 2 — still picks 2 even if all tied
        assert len(bottom) == 2

    def test_mixed_scored_and_unscored_ranking(self):
        pool = StrategyPool()
        pool.add(_make_strategy("scored1"))
        pool.add(_make_strategy("scored2"))
        pool.add(_make_strategy("unscored"))
        pool.update_score("scored1", _make_score("scored1", sharpe=2.0))
        pool.update_score("scored2", _make_score("scored2", sharpe=1.0))
        ranked = pool.ranked()
        # / scored strategies first, unscored last
        assert ranked[0].strategy.strategy_id == "scored1"
        assert ranked[1].strategy.strategy_id == "scored2"
        assert ranked[2].strategy.strategy_id == "unscored"

    def test_large_pool_bottom_quartile(self):
        pool = _pool_with_n(100)
        bottom = pool.bottom_quartile()
        assert len(bottom) == 25

    def test_remove_then_re_add(self):
        pool = StrategyPool()
        pool.add(_make_strategy("s1"))
        pool.update_score("s1", _make_score("s1", sharpe=5.0))
        pool.remove("s1")
        pool.add(_make_strategy("s1"))
        # / re-added strategy has no score
        assert pool.get("s1").score is None


# ────────────────────────────────────────────────────────────────
# deep failure-pinpointing tests — composite score
# ────────────────────────────────────────────────────────────────

class TestCompositeScoreDeep:
    def test_exact_formula_verification(self):
        # / sharpe=1.5, win_rate=0.6, max_dd=-0.15, brier=0.18
        # / score = 1.5*0.4 + 0.6*0.3 - 0.15*0.2 + (0.25-0.18)*0.1
        # /       = 0.6 + 0.18 - 0.03 + 0.007 = 0.757
        result = compute_composite_score(
            sharpe=1.5, win_rate=0.6, max_drawdown=-0.15, brier=0.18,
        )
        expected = 1.5 * 0.4 + 0.6 * 0.3 - 0.15 * 0.2 + (0.25 - 0.18) * 0.1
        assert expected == pytest.approx(0.757)
        assert result == pytest.approx(expected)

    def test_negative_sharpe_penalizes(self):
        # / negative sharpe should produce lower score
        positive = compute_composite_score(sharpe=1.0, win_rate=0.5, max_drawdown=0.1)
        negative = compute_composite_score(sharpe=-1.0, win_rate=0.5, max_drawdown=0.1)
        assert negative < positive
        # / verify exact: -1.0*0.4 + 0.5*0.3 - 0.1*0.2 = -0.4+0.15-0.02 = -0.27
        assert negative == pytest.approx(-1.0 * 0.4 + 0.5 * 0.3 - 0.1 * 0.2)

    def test_high_drawdown_penalizes(self):
        # / higher drawdown should reduce score
        low_dd = compute_composite_score(sharpe=1.0, win_rate=0.5, max_drawdown=0.05)
        high_dd = compute_composite_score(sharpe=1.0, win_rate=0.5, max_drawdown=0.50)
        assert high_dd < low_dd

    def test_weights_sum_check(self):
        # / weights: 0.4 + 0.3 + 0.2 + 0.1 = 1.0
        assert pytest.approx(1.0) == 0.4 + 0.3 + 0.2 + 0.1


# ────────────────────────────────────────────────────────────────
# deep failure-pinpointing tests — bottom quartile
# ────────────────────────────────────────────────────────────────

class TestBottomQuartileDeep:
    def test_exactly_4_strategies_returns_1(self):
        # / 4 // 4 = 1, max(1, 1) = 1
        pool = _pool_with_n(4)
        bottom = pool.bottom_quartile()
        assert len(bottom) == 1

    def test_5_strategies_returns_1(self):
        # / 5 // 4 = 1, max(1, 1) = 1
        pool = _pool_with_n(5)
        bottom = pool.bottom_quartile()
        assert len(bottom) == 1
        # / should be the worst scorer (s0 has sharpe=0.0)
        assert bottom[0].strategy.strategy_id == "s0"

    def test_8_strategies_returns_2(self):
        # / 8 // 4 = 2, max(1, 2) = 2
        pool = _pool_with_n(8)
        bottom = pool.bottom_quartile()
        assert len(bottom) == 2
        ids = {e.strategy.strategy_id for e in bottom}
        assert ids == {"s0", "s1"}


# ────────────────────────────────────────────────────────────────
# deep failure-pinpointing tests — ranked
# ────────────────────────────────────────────────────────────────

class TestRankedDeep:
    def test_unscored_last_with_inf(self):
        # / unscored strategies use float("-inf") as sort key
        pool = StrategyPool()
        pool.add(_make_strategy("scored"))
        pool.add(_make_strategy("unscored"))
        # / give scored strategy a very negative composite score
        score = _make_score("scored", sharpe=-10.0, win_rate=0.0, max_drawdown=0.5)
        pool.update_score("scored", score)
        ranked = pool.ranked()
        # / even with terrible score, scored should be above unscored (-inf)
        assert ranked[0].strategy.strategy_id == "scored"
        assert ranked[1].strategy.strategy_id == "unscored"
        assert ranked[1].score is None

    def test_tie_handling(self):
        # / strategies with identical scores should all appear in ranking
        pool = StrategyPool()
        for i in range(5):
            sid = f"tie_{i}"
            pool.add(_make_strategy(sid))
            pool.update_score(sid, _make_score(sid, sharpe=1.0, win_rate=0.5, max_drawdown=0.1))
        ranked = pool.ranked()
        assert len(ranked) == 5
        # / all composites should be identical
        scores = [e.score.composite_score for e in ranked]
        assert all(s == pytest.approx(scores[0]) for s in scores)

# / tests for post-analysis trigger on close in ExecutorAgent

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.executor_agent import _post_mortem_trigger, _spawn_post_mortem
from src.agents.task_tracker import ExecutorTaskTracker


def _clean_env() -> dict:
    return {
        k: v for k, v in os.environ.items()
        if k not in {
            "POST_MORTEM_PNL_ABS", "POST_MORTEM_PNL_PCT",
            "POST_MORTEM_WIN_ABS", "POST_MORTEM_WIN_PCT",
        }
    }


# ──────────────────────────────────────────────────────
# _post_mortem_trigger — direction + threshold gate
# ──────────────────────────────────────────────────────

class TestPostMortemTrigger:
    def test_none_pnl_no_trigger(self):
        assert _post_mortem_trigger(None, 1000.0) is None

    def test_zero_pnl_no_trigger(self):
        with patch.dict(os.environ, _clean_env(), clear=True):
            assert _post_mortem_trigger(0.0, 1000.0) is None

    def test_small_loss_under_both_thresholds(self):
        # / -$5 on $1000 notional = 0.5% — under 1% loss_pct and under $10 abs
        with patch.dict(os.environ, _clean_env(), clear=True):
            assert _post_mortem_trigger(-5.0, 1000.0) is None

    def test_loss_over_abs_fires(self):
        # / -$15 → over $10 default abs floor
        with patch.dict(os.environ, _clean_env(), clear=True):
            assert _post_mortem_trigger(-15.0, 5000.0) == "loss_threshold"

    def test_loss_over_pct_fires(self):
        # / -$8 on $500 notional = 1.6% — over 1% pct, under $10 abs
        with patch.dict(os.environ, _clean_env(), clear=True):
            assert _post_mortem_trigger(-8.0, 500.0) == "loss_threshold"

    def test_small_win_under_both_thresholds(self):
        # / +$15 on $1000 notional = 1.5% — under 2% win_pct and under $20 abs
        with patch.dict(os.environ, _clean_env(), clear=True):
            assert _post_mortem_trigger(15.0, 1000.0) is None

    def test_win_over_abs_fires(self):
        # / +$30 → over $20 default win abs
        with patch.dict(os.environ, _clean_env(), clear=True):
            assert _post_mortem_trigger(30.0, 5000.0) == "win_threshold"

    def test_win_over_pct_fires(self):
        # / +$15 on $500 notional = 3% — over 2% pct, under $20 abs
        with patch.dict(os.environ, _clean_env(), clear=True):
            assert _post_mortem_trigger(15.0, 500.0) == "win_threshold"

    def test_env_overrides_loss(self):
        with patch.dict(os.environ, {"POST_MORTEM_PNL_ABS": "100", "POST_MORTEM_PNL_PCT": "0.10"}):
            assert _post_mortem_trigger(-60.0, 1000.0) is None

    def test_env_overrides_win(self):
        with patch.dict(os.environ, {"POST_MORTEM_WIN_ABS": "200", "POST_MORTEM_WIN_PCT": "0.10"}):
            assert _post_mortem_trigger(80.0, 1000.0) is None

    def test_zero_notional_still_fires_on_abs(self):
        with patch.dict(os.environ, _clean_env(), clear=True):
            assert _post_mortem_trigger(-15.0, 0.0) == "loss_threshold"
            assert _post_mortem_trigger(30.0, 0.0) == "win_threshold"


# ──────────────────────────────────────────────────────
# _spawn_post_mortem
# ──────────────────────────────────────────────────────

class TestSpawnPostMortem:
    @pytest.mark.asyncio
    async def test_launches_write_task(self):
        mock_pool = MagicMock()
        fake_write = AsyncMock(return_value=True)
        tracker = ExecutorTaskTracker()

        with patch("src.knowledge.post_mortem_writer.write_post_mortem", new=fake_write):
            _spawn_post_mortem(tracker, mock_pool, 5, "sid", "AAPL", -80.0, "loss_threshold")
            import asyncio
            await asyncio.sleep(0)

        fake_write.assert_awaited_once()
        kwargs = fake_write.await_args.kwargs
        assert kwargs["strategy_id"] == "sid"
        assert kwargs["symbol"] == "AAPL"
        assert kwargs["pnl"] == -80.0
        assert kwargs["trigger_type"] == "loss_threshold"

    @pytest.mark.asyncio
    async def test_passes_win_trigger(self):
        mock_pool = MagicMock()
        fake_write = AsyncMock(return_value=True)
        tracker = ExecutorTaskTracker()

        with patch("src.knowledge.post_mortem_writer.write_post_mortem", new=fake_write):
            _spawn_post_mortem(tracker, mock_pool, 9, "sid", "TSLA", 250.0, "win_threshold")
            import asyncio
            await asyncio.sleep(0)

        fake_write.assert_awaited_once()
        assert fake_write.await_args.kwargs["trigger_type"] == "win_threshold"
        assert fake_write.await_args.kwargs["pnl"] == 250.0

    def test_missing_strategy_id_skips(self):
        tracker = ExecutorTaskTracker()
        with patch("src.knowledge.post_mortem_writer.write_post_mortem") as mock_write:
            _spawn_post_mortem(tracker, MagicMock(), 1, "", "AAPL", -100.0, "t")
            _spawn_post_mortem(tracker, MagicMock(), 1, None, "AAPL", -100.0, "t")
        mock_write.assert_not_called()

    def test_none_pnl_skips(self):
        tracker = ExecutorTaskTracker()
        with patch("src.knowledge.post_mortem_writer.write_post_mortem") as mock_write:
            _spawn_post_mortem(tracker, MagicMock(), 1, "sid", "AAPL", None, "t")
        mock_write.assert_not_called()

# / tests for post-mortem trigger on loss close in ExecutorAgent

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.executor_agent import _should_trigger_post_mortem, _spawn_post_mortem
from src.agents.task_tracker import ExecutorTaskTracker

# ──────────────────────────────────────────────────────
# _should_trigger_post_mortem — the loss threshold gate
# ──────────────────────────────────────────────────────

class TestShouldTriggerPostMortem:
    def test_positive_pnl_never_triggers(self):
        # / winning trade must NOT fire
        assert _should_trigger_post_mortem(pnl=100.0, entry_notional=1000.0) is False
        assert _should_trigger_post_mortem(pnl=0.01, entry_notional=1000.0) is False

    def test_zero_pnl_no_trigger(self):
        assert _should_trigger_post_mortem(pnl=0.0, entry_notional=1000.0) is False

    def test_none_pnl_no_trigger(self):
        assert _should_trigger_post_mortem(pnl=None, entry_notional=1000.0) is False

    def test_small_loss_below_both_thresholds_no_trigger(self):
        # / $20 loss on $2000 notional → under $50 AND under 2% (20/2000=1%) → no trigger
        env = {k: v for k, v in os.environ.items()
               if k not in ("POST_MORTEM_PNL_ABS", "POST_MORTEM_PNL_PCT")}
        with patch.dict(os.environ, env, clear=True):
            assert _should_trigger_post_mortem(pnl=-20.0, entry_notional=2000.0) is False

    def test_loss_gt_50_abs_triggers(self):
        # / pnl = -$60 → > $50 abs → fire
        env = {k: v for k, v in os.environ.items()
               if k not in ("POST_MORTEM_PNL_ABS", "POST_MORTEM_PNL_PCT")}
        with patch.dict(os.environ, env, clear=True):
            assert _should_trigger_post_mortem(pnl=-60.0, entry_notional=10000.0) is True

    def test_loss_gt_2pct_but_lt_50_abs_triggers(self):
        # / $30 loss on $1000 notional → 3% > 2% → fire (even though <$50)
        env = {k: v for k, v in os.environ.items()
               if k not in ("POST_MORTEM_PNL_ABS", "POST_MORTEM_PNL_PCT")}
        with patch.dict(os.environ, env, clear=True):
            assert _should_trigger_post_mortem(pnl=-30.0, entry_notional=1000.0) is True

    def test_env_override_abs_threshold(self):
        # / set $100 threshold → $60 loss no longer triggers on abs
        with patch.dict(os.environ, {"POST_MORTEM_PNL_ABS": "100", "POST_MORTEM_PNL_PCT": "0.10"}):
            assert _should_trigger_post_mortem(pnl=-60.0, entry_notional=1000.0) is False

    def test_env_override_pct_threshold(self):
        # / 5% threshold → 3% loss no longer triggers
        with patch.dict(os.environ, {"POST_MORTEM_PNL_ABS": "100", "POST_MORTEM_PNL_PCT": "0.05"}):
            assert _should_trigger_post_mortem(pnl=-30.0, entry_notional=1000.0) is False

    def test_zero_entry_notional_still_triggers_on_abs(self):
        # / when entry_notional is 0 (unknown), $60 loss still fires on abs threshold
        env = {k: v for k, v in os.environ.items()
               if k not in ("POST_MORTEM_PNL_ABS", "POST_MORTEM_PNL_PCT")}
        with patch.dict(os.environ, env, clear=True):
            assert _should_trigger_post_mortem(pnl=-60.0, entry_notional=0.0) is True


# ──────────────────────────────────────────────────────
# _spawn_post_mortem — fire-and-forget guards
# ──────────────────────────────────────────────────────

class TestSpawnPostMortem:
    @pytest.mark.asyncio
    async def test_launches_write_task(self):
        # / spawn must invoke tracker.spawn(write_post_mortem(...))
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

    def test_missing_strategy_id_skips(self):
        # / no strategy_id → no-op, no task
        tracker = ExecutorTaskTracker()
        with patch("src.knowledge.post_mortem_writer.write_post_mortem") as mock_write:
            _spawn_post_mortem(tracker, MagicMock(), 1, "", "AAPL", -100.0, "t")
            _spawn_post_mortem(tracker, MagicMock(), 1, None, "AAPL", -100.0, "t")
        mock_write.assert_not_called()

    def test_none_pnl_skips(self):
        # / pnl=None → skip, no task
        tracker = ExecutorTaskTracker()
        with patch("src.knowledge.post_mortem_writer.write_post_mortem") as mock_write:
            _spawn_post_mortem(tracker, MagicMock(), 1, "sid", "AAPL", None, "t")
        mock_write.assert_not_called()

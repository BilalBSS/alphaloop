# / tests for can_write_post_mortem — 24h per-strategy guard

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.knowledge.cooldown import can_write_post_mortem


def _mock_pool(fetchval_return=None, exc: Exception | None = None):
    mock_conn = AsyncMock()
    if exc is not None:
        mock_conn.fetchval = AsyncMock(side_effect=exc)
    else:
        mock_conn.fetchval = AsyncMock(return_value=fetchval_return)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool, mock_conn


# ──────────────────────────────────────────────────────
# baseline behaviour
# ──────────────────────────────────────────────────────

class TestCooldown:
    @pytest.mark.asyncio
    async def test_no_prior_post_mortem_returns_true(self):
        # / fetchval returns None when table has no row for strategy_id → allow write
        pool, _ = _mock_pool(fetchval_return=None)
        assert await can_write_post_mortem(pool, "strat_1") is True

    @pytest.mark.asyncio
    async def test_within_24h_returns_false(self):
        # / age = 5h < 24h → False
        pool, _ = _mock_pool(fetchval_return=5.0)
        assert await can_write_post_mortem(pool, "strat_1") is False

    @pytest.mark.asyncio
    async def test_greater_than_24h_returns_true(self):
        # / age = 30h > 24h → True
        pool, _ = _mock_pool(fetchval_return=30.0)
        assert await can_write_post_mortem(pool, "strat_1") is True

    @pytest.mark.asyncio
    async def test_exactly_at_boundary_allows_write(self):
        # / age = 24.0 exactly → >= 24 → True (the >= operator)
        pool, _ = _mock_pool(fetchval_return=24.0)
        assert await can_write_post_mortem(pool, "strat_1", hours=24) is True

    @pytest.mark.asyncio
    async def test_just_below_boundary_blocks_write(self):
        # / age = 23.999 → False
        pool, _ = _mock_pool(fetchval_return=23.999)
        assert await can_write_post_mortem(pool, "strat_1", hours=24) is False

    @pytest.mark.asyncio
    async def test_custom_hours_window(self):
        # / hours=1 → 0.5h < 1h → False; hours=1 → 2h > 1h → True
        pool1, _ = _mock_pool(fetchval_return=0.5)
        assert await can_write_post_mortem(pool1, "s", hours=1) is False
        pool2, _ = _mock_pool(fetchval_return=2.0)
        assert await can_write_post_mortem(pool2, "s", hours=1) is True

    @pytest.mark.asyncio
    async def test_empty_strategy_id_returns_false(self):
        # / no strategy_id → fail-closed, no db call
        pool, conn = _mock_pool(fetchval_return=None)
        assert await can_write_post_mortem(pool, "") is False
        conn.fetchval.assert_not_called()

    @pytest.mark.asyncio
    async def test_db_exception_fails_closed(self):
        # / any exception reading cooldown → return False (don't write under uncertainty)
        pool, _ = _mock_pool(exc=RuntimeError("connection lost"))
        assert await can_write_post_mortem(pool, "strat_1") is False

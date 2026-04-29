from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.data.retention import prune_observation_log, prune_system_events


def _mock_pool(mock_conn):
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool


class TestPruneObservationLog:
    @pytest.mark.asyncio
    async def test_returns_deleted_count(self):
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "DELETE 42"
        pool = _mock_pool(mock_conn)

        count = await prune_observation_log(pool, max_age_days=14)
        assert count == 42

    @pytest.mark.asyncio
    async def test_default_retention_is_14_days(self):
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "DELETE 0"
        pool = _mock_pool(mock_conn)

        await prune_observation_log(pool)
        sql = mock_conn.execute.call_args.args[0]
        assert "14 days" in sql
        assert "observation_log" in sql
        assert "created_at" in sql

    @pytest.mark.asyncio
    async def test_custom_retention_days(self):
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "DELETE 5"
        pool = _mock_pool(mock_conn)

        count = await prune_observation_log(pool, max_age_days=7)
        sql = mock_conn.execute.call_args.args[0]
        assert "7 days" in sql
        assert count == 5

    @pytest.mark.asyncio
    async def test_zero_count_returns_zero(self):
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "DELETE 0"
        pool = _mock_pool(mock_conn)

        count = await prune_observation_log(pool)
        assert count == 0

    @pytest.mark.asyncio
    async def test_undefined_table_tolerated(self):
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception('relation "observation_log" does not exist')
        pool = _mock_pool(mock_conn)

        count = await prune_observation_log(pool)
        assert count == 0

    @pytest.mark.asyncio
    async def test_other_errors_raise(self):
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception("connection lost")
        pool = _mock_pool(mock_conn)

        with pytest.raises(Exception, match="connection lost"):
            await prune_observation_log(pool)

    @pytest.mark.asyncio
    async def test_zero_or_negative_days_rejected(self):
        pool = _mock_pool(AsyncMock())
        with pytest.raises(ValueError):
            await prune_observation_log(pool, max_age_days=0)
        with pytest.raises(ValueError):
            await prune_observation_log(pool, max_age_days=-3)

    @pytest.mark.asyncio
    async def test_days_coerced_to_int(self):
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "DELETE 0"
        pool = _mock_pool(mock_conn)

        await prune_observation_log(pool, max_age_days=21.7)
        sql = mock_conn.execute.call_args.args[0]
        assert "21 days" in sql


class TestPruneSystemEvents:
    @pytest.mark.asyncio
    async def test_returns_deleted_count(self):
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "DELETE 100"
        pool = _mock_pool(mock_conn)

        count = await prune_system_events(pool, max_age_days=30)
        assert count == 100

    @pytest.mark.asyncio
    async def test_default_retention_is_30_days(self):
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "DELETE 0"
        pool = _mock_pool(mock_conn)

        await prune_system_events(pool)
        sql = mock_conn.execute.call_args.args[0]
        assert "30 days" in sql
        assert "system_events" in sql
        assert "timestamp" in sql

    @pytest.mark.asyncio
    async def test_custom_retention_days(self):
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "DELETE 7"
        pool = _mock_pool(mock_conn)

        count = await prune_system_events(pool, max_age_days=60)
        sql = mock_conn.execute.call_args.args[0]
        assert "60 days" in sql
        assert count == 7

    @pytest.mark.asyncio
    async def test_undefined_table_tolerated(self):
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception("undefined table system_events")
        pool = _mock_pool(mock_conn)

        count = await prune_system_events(pool)
        assert count == 0

    @pytest.mark.asyncio
    async def test_other_errors_raise(self):
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception("disk full")
        pool = _mock_pool(mock_conn)

        with pytest.raises(Exception, match="disk full"):
            await prune_system_events(pool)

    @pytest.mark.asyncio
    async def test_zero_or_negative_days_rejected(self):
        pool = _mock_pool(AsyncMock())
        with pytest.raises(ValueError):
            await prune_system_events(pool, max_age_days=0)
        with pytest.raises(ValueError):
            await prune_system_events(pool, max_age_days=-1)

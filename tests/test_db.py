# / tests for db module — pool init, migrations, url masking

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.data.db as db_module
from src.data.db import _mask_url, _run_migrations, close_db, get_pool, init_db


@pytest.fixture(autouse=True)
def reset_pool():
    # / reset global pool state between tests
    db_module._pool = None
    yield
    db_module._pool = None


class TestMaskUrl:
    def test_masks_password(self):
        url = "postgresql://user:secret@host:5432/db"
        result = _mask_url(url)
        assert "secret" not in result
        assert "***" in result
        assert "host:5432" in result

    def test_no_password(self):
        url = "postgresql://host:5432/db"
        assert _mask_url(url) == "postgresql://host:5432/db"

    def test_complex_url(self):
        url = "postgresql://neondb_owner:npg_abc123@ep-pooler.neon.tech/neondb?sslmode=require"
        result = _mask_url(url)
        assert "npg_abc123" not in result
        assert "***" in result
        assert "ep-pooler.neon.tech" in result

    def test_encoded_password(self):
        url = "postgresql://user:p%40ss%3Aword@host:5432/db"
        result = _mask_url(url)
        assert "p%40ss" not in result
        assert "***" in result

    def test_url_with_no_port(self):
        url = "postgresql://user:secret@host/db"
        result = _mask_url(url)
        assert "secret" not in result
        assert "***" in result
        assert "host" in result

    def test_url_with_special_chars_in_password(self):
        url = "postgresql://user:p%40ss%23w0rd!@host:5432/db"
        result = _mask_url(url)
        assert "p%40ss" not in result
        assert "***" in result

    def test_malformed_url_returns_original(self):
        url = "not-a-valid-url"
        assert _mask_url(url) == url


class TestGetPool:
    @pytest.mark.asyncio
    async def test_raises_when_not_initialized(self):
        with pytest.raises(RuntimeError, match="not initialized"):
            await get_pool()

    @pytest.mark.asyncio
    async def test_returns_pool_when_initialized(self):
        fake_pool = MagicMock()
        db_module._pool = fake_pool
        result = await get_pool()
        assert result is fake_pool


class TestCloseDb:
    @pytest.mark.asyncio
    async def test_closes_pool(self):
        fake_pool = AsyncMock()
        db_module._pool = fake_pool
        await close_db()
        fake_pool.close.assert_awaited_once()
        assert db_module._pool is None

    @pytest.mark.asyncio
    async def test_noop_when_no_pool(self):
        # / should not raise
        await close_db()
        assert db_module._pool is None


class TestInitDb:
    @pytest.mark.asyncio
    async def test_raises_without_url(self):
        with patch.dict("os.environ", {}, clear=True), pytest.raises(RuntimeError, match="DATABASE_URL"):
            await init_db()

    @pytest.mark.asyncio
    async def test_returns_existing_pool(self):
        fake_pool = MagicMock()
        db_module._pool = fake_pool
        result = await init_db("postgresql://fake")
        assert result is fake_pool

    @pytest.mark.asyncio
    async def test_creates_pool(self):
        fake_pool = AsyncMock()
        with patch("src.data.db.asyncpg") as mock_asyncpg:
            mock_asyncpg.create_pool = AsyncMock(return_value=fake_pool)
            with patch("src.data.db._run_migrations", new_callable=AsyncMock):
                result = await init_db("postgresql://user:pass@host/db")
                assert result is fake_pool
                mock_asyncpg.create_pool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_concurrent_init_db_returns_same_pool(self):
        # / two concurrent calls should return the same pool instance
        fake_pool = AsyncMock()
        with patch("src.data.db.asyncpg") as mock_asyncpg:
            mock_asyncpg.create_pool = AsyncMock(return_value=fake_pool)
            with patch("src.data.db._run_migrations", new_callable=AsyncMock):
                results = await asyncio.gather(
                    init_db("postgresql://user:pass@host/db"),
                    init_db("postgresql://user:pass@host/db"),
                )
                assert results[0] is results[1]
                # / create_pool should only be called once despite two concurrent calls
                assert mock_asyncpg.create_pool.await_count == 1

    @pytest.mark.asyncio
    async def test_reads_database_url_from_env(self):
        fake_pool = AsyncMock()
        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://env:pass@host/db"}):
            with patch("src.data.db.asyncpg") as mock_asyncpg:
                mock_asyncpg.create_pool = AsyncMock(return_value=fake_pool)
                with patch("src.data.db._run_migrations", new_callable=AsyncMock):
                    result = await init_db()
                    assert result is fake_pool
                    call_kwargs = mock_asyncpg.create_pool.await_args
                    assert call_kwargs[0][0] == "postgresql://env:pass@host/db"
                    assert call_kwargs[1]["min_size"] == 2
                    assert call_kwargs[1]["init"] is not None


class TestRunMigrations:
    @pytest.mark.asyncio
    async def test_creates_migrations_table(self):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        # / asyncpg pool.acquire() returns an async context manager
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_pool = MagicMock()
        mock_pool.acquire.return_value = mock_ctx

        with patch("src.data.db.MIGRATIONS_DIR") as mock_dir:
            mock_dir.exists.return_value = False
            await _run_migrations(mock_pool)

        # / should have called execute to create _migrations table
        calls = [str(c) for c in mock_conn.execute.call_args_list]
        assert any("_migrations" in c for c in calls)

    @pytest.mark.asyncio
    async def test_skips_already_applied_migrations(self):
        mock_conn = AsyncMock()
        # / simulate that 001_initial.sql has already been applied
        mock_conn.fetch = AsyncMock(return_value=[{"filename": "001_initial.sql"}])

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_pool = MagicMock()
        mock_pool.acquire.return_value = mock_ctx

        with patch("src.data.db.MIGRATIONS_DIR") as mock_dir:
            mock_file = MagicMock()
            mock_file.name = "001_initial.sql"
            mock_dir.exists.return_value = True
            mock_dir.glob.return_value = [mock_file]
            await _run_migrations(mock_pool)

        # / should not have read or executed the migration file
        mock_file.read_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_missing_sql_file_gracefully(self):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_pool = MagicMock()
        mock_pool.acquire.return_value = mock_ctx

        with patch("src.data.db.MIGRATIONS_DIR") as mock_dir:
            # / migrations dir does not exist
            mock_dir.exists.return_value = False
            # / should return gracefully, no exception
            await _run_migrations(mock_pool)

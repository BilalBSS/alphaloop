# / tests for src/knowledge/loops.py — embedding backfill + archive upkeep loops

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.knowledge import loops
from src.knowledge.loops import (
    ARCHIVE_LOOP_INTERVAL,
    ARCHIVE_OLDER_THAN_DAYS,
    EMBED_BACKFILL_BATCH,
    EMBED_LOOP_INTERVAL,
    _embed_backfill_once,
    _embed_one_document,
)


def _mock_pool(fetch_return=None):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


# ──────────────────────────────────────────────────────
# module constants
# ──────────────────────────────────────────────────────

class TestConstants:
    def test_embed_interval_is_six_hours(self):
        assert EMBED_LOOP_INTERVAL == 6 * 60 * 60

    def test_archive_interval_is_twenty_four_hours(self):
        assert ARCHIVE_LOOP_INTERVAL == 24 * 60 * 60

    def test_archive_retention_is_180_days(self):
        assert ARCHIVE_OLDER_THAN_DAYS == 180

    def test_embed_batch_is_20(self):
        assert EMBED_BACKFILL_BATCH == 20


# ──────────────────────────────────────────────────────
# _embed_one_document — happy path + failure modes
# ──────────────────────────────────────────────────────

class TestEmbedOneDocument:
    @pytest.mark.asyncio
    async def test_happy_path_returns_true(self):
        pool, _ = _mock_pool()
        writer = MagicMock()
        writer.read_document = AsyncMock(return_value="# doc\n\nbody text here")
        embedder = MagicMock()
        embedder.embed_batch = AsyncMock(return_value=[[0.1] * 768])
        store = MagicMock()
        store.upsert_chunks = AsyncMock(return_value=1)

        doc = {"id": 5, "path": "regimes/bull.md", "category": "regimes"}
        with patch(
            "src.knowledge.loops.chunk_markdown", return_value=["chunk 1"],
        ):
            ok = await _embed_one_document(pool, embedder, store, writer, doc)
        assert ok is True
        store.upsert_chunks.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_file_returns_false(self):
        pool, _ = _mock_pool()
        writer = MagicMock()
        writer.read_document = AsyncMock(return_value=None)
        embedder = MagicMock()
        embedder.embed_batch = AsyncMock()
        store = MagicMock()
        store.upsert_chunks = AsyncMock()

        doc = {"id": 5, "path": "regimes/gone.md"}
        ok = await _embed_one_document(pool, embedder, store, writer, doc)
        assert ok is False
        # / never embedded; never upserted
        embedder.embed_batch.assert_not_called()
        store.upsert_chunks.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_chunks_returns_false(self):
        pool, _ = _mock_pool()
        writer = MagicMock()
        writer.read_document = AsyncMock(return_value="   ")
        embedder = MagicMock()
        embedder.embed_batch = AsyncMock()
        store = MagicMock()
        store.upsert_chunks = AsyncMock()

        doc = {"id": 5, "path": "regimes/empty.md"}
        with patch("src.knowledge.loops.chunk_markdown", return_value=[]):
            ok = await _embed_one_document(pool, embedder, store, writer, doc)
        assert ok is False
        embedder.embed_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_written_returns_false(self):
        # / upsert_chunks returns 0 (e.g., all embed vectors were None)
        pool, _ = _mock_pool()
        writer = MagicMock()
        writer.read_document = AsyncMock(return_value="content")
        embedder = MagicMock()
        embedder.embed_batch = AsyncMock(return_value=[None])
        store = MagicMock()
        store.upsert_chunks = AsyncMock(return_value=0)

        doc = {"id": 1, "path": "x.md"}
        with patch("src.knowledge.loops.chunk_markdown", return_value=["c"]):
            ok = await _embed_one_document(pool, embedder, store, writer, doc)
        assert ok is False


# ──────────────────────────────────────────────────────
# _embed_backfill_once — orchestration
# ──────────────────────────────────────────────────────

class TestEmbedBackfillOnce:
    @pytest.mark.asyncio
    async def test_no_rows_returns_zero(self):
        pool, _conn = _mock_pool(fetch_return=[])
        embedder_instance = MagicMock()
        embedder_instance.close = AsyncMock()
        with patch(
            "src.knowledge.loops.OllamaEmbedder", return_value=embedder_instance,
        ), patch(
            "src.knowledge.loops.VectorStore",
        ), patch(
            "src.knowledge.loops.WikiWriter",
        ):
            done = await _embed_backfill_once(pool)
        assert done == 0
        # / embedder closed even with no rows
        embedder_instance.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_query_uses_batch_limit(self):
        pool, conn = _mock_pool(fetch_return=[])
        embedder_instance = MagicMock()
        embedder_instance.close = AsyncMock()
        with patch(
            "src.knowledge.loops.OllamaEmbedder", return_value=embedder_instance,
        ), patch(
            "src.knowledge.loops.VectorStore",
        ), patch(
            "src.knowledge.loops.WikiWriter",
        ):
            await _embed_backfill_once(pool)
        # / fetch called with the batch size
        args = conn.fetch.await_args.args
        assert args[-1] == EMBED_BACKFILL_BATCH
        # / sql picks rows missing embeddings
        sql = args[0]
        assert "LEFT JOIN wiki_embeddings" in sql
        assert "e.id IS NULL" in sql

    @pytest.mark.asyncio
    async def test_fetch_error_returns_zero_and_still_closes(self):
        pool = MagicMock()
        conn = MagicMock()
        conn.fetch = AsyncMock(side_effect=RuntimeError("db down"))
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = MagicMock(return_value=cm)

        embedder_instance = MagicMock()
        embedder_instance.close = AsyncMock()
        with patch(
            "src.knowledge.loops.OllamaEmbedder", return_value=embedder_instance,
        ), patch(
            "src.knowledge.loops.VectorStore",
        ), patch(
            "src.knowledge.loops.WikiWriter",
        ):
            done = await _embed_backfill_once(pool)
        assert done == 0
        # / even on fetch failure, embedder.close must run (finally block)
        embedder_instance.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_processes_all_rows_and_counts_successes(self):
        rows = [
            {"id": 1, "path": "a.md", "category": "regimes"},
            {"id": 2, "path": "b.md", "category": "regimes"},
            {"id": 3, "path": "c.md", "category": "regimes"},
        ]
        pool, _ = _mock_pool(fetch_return=rows)
        embedder_instance = MagicMock()
        embedder_instance.close = AsyncMock()

        # / _embed_one_document results: True, False (missing), True
        with patch(
            "src.knowledge.loops.OllamaEmbedder", return_value=embedder_instance,
        ), patch(
            "src.knowledge.loops.VectorStore",
        ), patch(
            "src.knowledge.loops.WikiWriter",
        ), patch(
            "src.knowledge.loops._embed_one_document",
            side_effect=[True, False, True],
        ) as m:
            done = await _embed_backfill_once(pool)
        assert done == 2
        assert m.await_count == 3

    @pytest.mark.asyncio
    async def test_per_doc_exception_is_swallowed(self):
        # / one failure must not abort the batch
        rows = [
            {"id": 1, "path": "a.md"},
            {"id": 2, "path": "b.md"},
        ]
        pool, _ = _mock_pool(fetch_return=rows)
        embedder_instance = MagicMock()
        embedder_instance.close = AsyncMock()

        with patch(
            "src.knowledge.loops.OllamaEmbedder", return_value=embedder_instance,
        ), patch(
            "src.knowledge.loops.VectorStore",
        ), patch(
            "src.knowledge.loops.WikiWriter",
        ), patch(
            "src.knowledge.loops._embed_one_document",
            side_effect=[RuntimeError("embed fail"), True],
        ):
            done = await _embed_backfill_once(pool)
        # / one succeeded, one failed but did not abort
        assert done == 1
        embedder_instance.close.assert_awaited_once()


# ──────────────────────────────────────────────────────
# wiki_embedding_backfill_loop — side-effect driver
# ──────────────────────────────────────────────────────

class TestEmbeddingBackfillLoop:
    @pytest.mark.asyncio
    async def test_one_shot_returns_work_count(self):
        # / the contract is one pass per call — orchestrator owns cadence.
        # / the old `while True` + asyncio.sleep pattern conflicted with the
        # / orchestrator's asyncio.wait_for wrapper so the outer timeout always
        # / fired. returning an int (documents embedded) is the signal.
        pool, _ = _mock_pool()

        async def _fake_once(_pool):
            return 7

        with patch(
            "src.knowledge.loops._embed_backfill_once", side_effect=_fake_once,
        ):
            result = await loops.wiki_embedding_backfill_loop(pool)
        assert result == 7

    @pytest.mark.asyncio
    async def test_one_shot_swallows_exception_and_returns_zero(self):
        # / exceptions in the inner pass must not escape — orchestrator's next
        # / cadence tick retries. returning 0 signals "no work done".
        pool, _ = _mock_pool()

        with patch(
            "src.knowledge.loops._embed_backfill_once",
            side_effect=RuntimeError("boom"),
        ):
            result = await loops.wiki_embedding_backfill_loop(pool)
        assert result == 0


# ──────────────────────────────────────────────────────
# wiki_archive_loop
# ──────────────────────────────────────────────────────

class TestArchiveLoop:
    @pytest.mark.asyncio
    async def test_one_shot_invokes_archive_with_retention(self):
        pool, _ = _mock_pool()

        writer_instance = MagicMock()
        writer_instance.archive_old = AsyncMock(return_value=3)

        with patch(
            "src.knowledge.loops.WikiWriter", return_value=writer_instance,
        ):
            moved = await loops.wiki_archive_loop(pool)
        writer_instance.archive_old.assert_awaited_once_with(
            older_than_days=ARCHIVE_OLDER_THAN_DAYS,
        )
        assert moved == 3

    @pytest.mark.asyncio
    async def test_one_shot_swallows_exception_and_returns_zero(self):
        pool, _ = _mock_pool()
        writer_instance = MagicMock()
        writer_instance.archive_old = AsyncMock(
            side_effect=RuntimeError("archive broke"),
        )

        with patch(
            "src.knowledge.loops.WikiWriter", return_value=writer_instance,
        ):
            moved = await loops.wiki_archive_loop(pool)
        assert moved == 0

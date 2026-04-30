# / tests for VectorStore — upsert_chunks, search, delete_by_document (mocked asyncpg)

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.knowledge.vector_store import VectorStore, _format_vector


def _mock_pool():
    # / build a pool whose conn supports .execute, .fetchrow, .fetch, and .transaction()
    mock_conn = AsyncMock()
    # / transaction() returns an async context manager
    tx_ctx = AsyncMock()
    tx_ctx.__aenter__.return_value = None
    tx_ctx.__aexit__.return_value = False
    mock_conn.transaction = MagicMock(return_value=tx_ctx)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool, mock_conn


# ──────────────────────────────────────────────────────
# _format_vector
# ──────────────────────────────────────────────────────

def test_format_vector_pgvector_literal_shape():
    # / "[0.1,0.2,0.3]" exact syntax pgvector expects
    out = _format_vector([0.1, 0.2, 0.3])
    assert out.startswith("[") and out.endswith("]")
    assert out.count(",") == 2


def test_format_vector_coerces_non_floats():
    out = _format_vector([1, 2, 3])  # / ints should serialize as floats
    assert "[" in out and "]" in out


# ──────────────────────────────────────────────────────
# upsert_chunks
# ──────────────────────────────────────────────────────

class TestUpsertChunks:
    @pytest.mark.asyncio
    async def test_empty_chunks_returns_zero_no_db_call(self):
        # / no chunks → fast-path, no SQL at all
        pool, conn = _mock_pool()
        store = VectorStore(pool)
        n = await store.upsert_chunks(document_id=1, chunks=[], embeddings=[])
        assert n == 0
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_length_mismatch_raises(self):
        pool, _conn = _mock_pool()
        store = VectorStore(pool)
        with pytest.raises(ValueError, match="length mismatch"):
            await store.upsert_chunks(
                document_id=1,
                chunks=["a", "b"],
                embeddings=[[0.1] * 768],
            )

    @pytest.mark.asyncio
    async def test_issues_delete_then_inserts(self):
        # / first call must be DELETE FROM wiki_embeddings … then INSERTs inside a txn
        pool, conn = _mock_pool()
        store = VectorStore(pool)
        chunks = ["a", "b", "c"]
        embs = [[0.1] * 768, [0.2] * 768, [0.3] * 768]
        written = await store.upsert_chunks(1, chunks, embs)

        assert written == 3
        # / first execute should be the DELETE
        first_sql = conn.execute.await_args_list[0].args[0]
        assert "DELETE FROM wiki_embeddings" in first_sql
        assert conn.execute.await_args_list[0].args[1] == 1  # / document_id
        # / remaining calls should be INSERTs
        insert_calls = conn.execute.await_args_list[1:]
        assert len(insert_calls) == 3
        for c in insert_calls:
            assert "INSERT INTO wiki_embeddings" in c.args[0]

    @pytest.mark.asyncio
    async def test_none_embedding_skipped(self):
        # / failed embed entries (None) must not insert a row
        pool, conn = _mock_pool()
        store = VectorStore(pool)
        chunks = ["ok1", "bad", "ok2"]
        embs = [[0.1] * 768, None, [0.2] * 768]

        written = await store.upsert_chunks(1, chunks, embs)
        assert written == 2  # / only the two non-None embeddings were inserted
        # / 1 DELETE + 2 INSERTs
        assert conn.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_empty_chunk_text_skipped(self):
        # / whitespace-only chunks should also be skipped even with a valid embedding
        pool, _conn = _mock_pool()
        store = VectorStore(pool)
        chunks = ["ok", "   "]
        embs = [[0.1] * 768, [0.2] * 768]

        written = await store.upsert_chunks(1, chunks, embs)
        assert written == 1

    @pytest.mark.asyncio
    async def test_runs_inside_transaction(self):
        # / transaction() must be entered so DELETE+INSERTs are atomic
        pool, conn = _mock_pool()
        store = VectorStore(pool)
        await store.upsert_chunks(1, ["x"], [[0.0] * 768])
        conn.transaction.assert_called_once()


# ──────────────────────────────────────────────────────
# delete_by_document
# ──────────────────────────────────────────────────────

class TestDeleteByDocument:
    @pytest.mark.asyncio
    async def test_issues_delete_statement(self):
        pool, conn = _mock_pool()
        conn.execute.return_value = "DELETE 3"
        store = VectorStore(pool)
        n = await store.delete_by_document(42)
        assert n == 3
        sql = conn.execute.await_args.args[0]
        assert "DELETE FROM wiki_embeddings" in sql
        assert conn.execute.await_args.args[1] == 42

    @pytest.mark.asyncio
    async def test_returns_zero_on_unparseable_status(self):
        pool, conn = _mock_pool()
        conn.execute.return_value = "weird"
        store = VectorStore(pool)
        assert await store.delete_by_document(1) == 0


# ──────────────────────────────────────────────────────
# search()
# ──────────────────────────────────────────────────────

class TestSearch:
    @pytest.mark.asyncio
    async def test_empty_query_embedding_returns_empty(self):
        pool, conn = _mock_pool()
        store = VectorStore(pool)
        assert await store.search([]) == []
        conn.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_cosine_operator_and_order_by(self):
        # / should use pgvector '<=>' cosine distance + ORDER BY that operator
        pool, conn = _mock_pool()
        conn.fetch.return_value = []
        store = VectorStore(pool)

        await store.search([0.1] * 768, top_k=5)
        sql = conn.fetch.await_args.args[0]
        assert "<=>" in sql
        assert "ORDER BY" in sql.upper()
        assert "LIMIT" in sql.upper()

    @pytest.mark.asyncio
    async def test_top_k_forwarded_to_limit(self):
        pool, conn = _mock_pool()
        conn.fetch.return_value = []
        store = VectorStore(pool)

        await store.search([0.1] * 768, top_k=17)
        # / last positional arg should be top_k (17)
        assert conn.fetch.await_args.args[-1] == 17

    @pytest.mark.asyncio
    async def test_symbols_filter_adds_where_clause(self):
        pool, conn = _mock_pool()
        conn.fetch.return_value = []
        store = VectorStore(pool)

        await store.search([0.1] * 768, top_k=3, symbols=["AAPL", "MSFT"])
        sql = conn.fetch.await_args.args[0]
        assert "WHERE" in sql.upper()
        assert "d.symbols &&" in sql
        # / symbols should appear in the parameter list
        assert ["AAPL", "MSFT"] in conn.fetch.await_args.args

    @pytest.mark.asyncio
    async def test_no_symbols_no_where_clause(self):
        pool, conn = _mock_pool()
        conn.fetch.return_value = []
        store = VectorStore(pool)

        await store.search([0.1] * 768, top_k=5, symbols=None)
        sql = conn.fetch.await_args.args[0]
        # / no user-provided WHERE (just the LIMIT/ORDER BY)
        assert "d.symbols &&" not in sql

    @pytest.mark.asyncio
    async def test_returns_dict_rows(self):
        pool, conn = _mock_pool()
        conn.fetch.return_value = [
            {"document_id": 1, "path": "x/a.md", "category": "meta",
             "title": "A", "symbols": ["AAPL"], "strategy_ids": [],
             "confidence": "high", "embedding_id": 10, "chunk_index": 0,
             "chunk_text": "hello", "distance": 0.15},
        ]
        store = VectorStore(pool)
        hits = await store.search([0.1] * 768, top_k=1)
        assert len(hits) == 1
        assert hits[0]["path"] == "x/a.md"
        assert hits[0]["distance"] == 0.15

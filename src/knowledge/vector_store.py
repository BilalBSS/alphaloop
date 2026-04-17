# / pgvector interface for wiki_embeddings — upsert chunks + cosine search

from __future__ import annotations

from typing import Any, Sequence

import structlog

logger = structlog.get_logger(__name__)


def _format_vector(vec: Sequence[float]) -> str:
    # / pgvector accepts literal strings like '[0.1,0.2,...]'
    return "[" + ",".join(f"{float(v):.8g}" for v in vec) + "]"


class VectorStore:
    # / wraps pgvector ops for wiki_embeddings and chart_analyses

    def __init__(self, pool):
        self._pool = pool

    async def upsert_chunks(
        self,
        document_id: int,
        chunks: list[str],
        embeddings: list[list[float] | None],
    ) -> int:
        # / delete old rows for document_id and insert fresh chunks inside a txn
        # / skips chunks whose embedding is None (failed embed)
        if not chunks:
            return 0
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"chunks/embeddings length mismatch: {len(chunks)} vs {len(embeddings)}",
            )

        written = 0
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM wiki_embeddings WHERE document_id = $1",
                    document_id,
                )
                for idx, (chunk_text, vec) in enumerate(zip(chunks, embeddings)):
                    if vec is None or not chunk_text.strip():
                        continue
                    await conn.execute(
                        """
                        INSERT INTO wiki_embeddings (document_id, chunk_index, chunk_text, embedding)
                        VALUES ($1, $2, $3, $4::vector)
                        """,
                        document_id, idx, chunk_text, _format_vector(vec),
                    )
                    written += 1
        logger.info(
            "wiki_embeddings_upserted",
            document_id=document_id, written=written, total=len(chunks),
        )
        return written

    async def delete_by_document(self, document_id: int) -> int:
        # / remove all chunk rows for a given document
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM wiki_embeddings WHERE document_id = $1",
                document_id,
            )
        try:
            count = int(result.split()[-1])
        except Exception:
            count = 0
        if count:
            logger.info("wiki_embeddings_deleted", document_id=document_id, count=count)
        return count

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        symbols: list[str] | None = None,
    ) -> list[dict]:
        # / cosine distance search joined to wiki_documents metadata
        if not query_embedding:
            return []
        vec_literal = _format_vector(query_embedding)

        params: list[Any] = [vec_literal]
        where_clauses: list[str] = []
        if symbols:
            params.append(symbols)
            where_clauses.append(f"d.symbols && ${len(params)}")
        params.append(int(top_k))

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        sql = f"""
            SELECT d.id AS document_id, d.path, d.category, d.title,
                   d.symbols, d.strategy_ids, d.confidence,
                   e.id AS embedding_id, e.chunk_index, e.chunk_text,
                   (e.embedding <=> $1::vector) AS distance
            FROM wiki_embeddings e
            JOIN wiki_documents d ON d.id = e.document_id
            {where_sql}
            ORDER BY e.embedding <=> $1::vector
            LIMIT ${len(params)}
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]

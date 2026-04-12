# / postgres tsvector-based full-text search over wiki_documents
# / hybrid search (tsvector + pgvector) lives in src.rag.retriever

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


class WikiSearch:
    # / tsvector keyword search — wraps websearch_to_tsquery for flexible queries

    def __init__(self, pool):
        self._pool = pool

    async def search(
        self,
        query: str,
        category: str | None = None,
        symbols: list[str] | None = None,
        top_k: int = 5,
    ) -> list[dict]:
        # / returns [{path, category, title, score, snippet}] ranked by ts_rank_cd
        if not query or not query.strip():
            return []

        clauses = ["content_tsv @@ websearch_to_tsquery('english', $1)"]
        params: list = [query]
        if category is not None:
            params.append(category)
            clauses.append(f"category = ${len(params)}")
        if symbols:
            params.append(symbols)
            clauses.append(f"symbols && ${len(params)}")
        params.append(top_k)

        sql = f"""
            SELECT id, path, category, title, symbols, strategy_ids, confidence,
                   word_count, updated_at,
                   ts_rank_cd(content_tsv, websearch_to_tsquery('english', $1)) AS score
            FROM wiki_documents
            WHERE {' AND '.join(clauses)}
            ORDER BY score DESC, updated_at DESC
            LIMIT ${len(params)}
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]

    async def search_by_category(
        self,
        category: str,
        query: str | None = None,
        top_k: int = 10,
    ) -> list[dict]:
        # / list recent docs in a category, optionally filtered by query
        if query:
            return await self.search(query, category=category, top_k=top_k)
        sql = """
            SELECT id, path, category, title, symbols, strategy_ids, confidence,
                   word_count, updated_at
            FROM wiki_documents
            WHERE category = $1
            ORDER BY updated_at DESC
            LIMIT $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, category, top_k)
        return [dict(r) for r in rows]

    async def count_by_category(self) -> dict[str, int]:
        sql = "SELECT category, count(*) AS n FROM wiki_documents GROUP BY category"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql)
        return {r["category"]: int(r["n"]) for r in rows}

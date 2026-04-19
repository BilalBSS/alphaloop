# / hybrid search: merges wiki tsvector + vector store via reciprocal rank fusion

from __future__ import annotations

import structlog

from src.knowledge.embedder import OllamaEmbedder
from src.knowledge.vector_store import VectorStore
from src.knowledge.wiki_search import WikiSearch

logger = structlog.get_logger(__name__)

# / rrf dampening constant — 60 is the commonly cited default
RRF_K = 60


class HybridRetriever:
    # / combines keyword ts_rank + vector cosine via reciprocal rank fusion

    def __init__(
        self,
        pool,
        embedder: OllamaEmbedder | None = None,
        wiki_search: WikiSearch | None = None,
        vector_store: VectorStore | None = None,
        k: int = RRF_K,
    ):
        self._pool = pool
        self._embedder = embedder or OllamaEmbedder()
        self._wiki = wiki_search or WikiSearch(pool)
        self._vec = vector_store or VectorStore(pool)
        self._k = int(k)

    async def search(
        self,
        query: str,
        top_k: int = 5,
        symbols: list[str] | None = None,
    ) -> list[dict]:
        # / merge tsvector + vector results by rrf, return top_k fused hits
        if not query or not query.strip():
            return []

        # / fetch both in parallel-friendly order (not gather — wiki is sync-db so cheap)
        keyword_hits = await self._wiki.search(query=query, symbols=symbols, top_k=top_k * 4)

        vec_hits: list[dict] = []
        query_vec = await self._embedder.embed(query)
        if query_vec is not None:
            try:
                vec_hits = await self._vec.search(query_vec, top_k=top_k * 4, symbols=symbols)
            except Exception as exc:
                logger.info("hybrid_vector_search_failed", error=str(exc)[:120])
                vec_hits = []

        fused = self._rrf(keyword_hits, vec_hits)
        return fused[:top_k]

    def _rrf(
        self, keyword_hits: list[dict], vec_hits: list[dict],
    ) -> list[dict]:
        # / reciprocal rank fusion keyed by document path
        scores: dict[str, float] = {}
        meta: dict[str, dict] = {}

        for rank, hit in enumerate(keyword_hits):
            path = hit.get("path")
            if not path:
                continue
            scores[path] = scores.get(path, 0.0) + 1.0 / (self._k + rank + 1)
            meta.setdefault(path, dict(hit))
            meta[path].setdefault("title", hit.get("title"))
            meta[path]["keyword_rank"] = rank + 1
            meta[path].setdefault("chunk_text", None)

        for rank, hit in enumerate(vec_hits):
            path = hit.get("path")
            if not path:
                continue
            scores[path] = scores.get(path, 0.0) + 1.0 / (self._k + rank + 1)
            entry = meta.setdefault(path, {})
            # / preserve keyword metadata where already present
            for key in ("category", "symbols", "strategy_ids", "confidence", "title"):
                if key not in entry or entry.get(key) is None:
                    entry[key] = hit.get(key)
            entry["path"] = path
            entry["vector_rank"] = rank + 1
            # / fill chunk_text from the best vector hit seen so far
            if entry.get("chunk_text") is None:
                entry["chunk_text"] = hit.get("chunk_text")
            entry.setdefault("document_id", hit.get("document_id"))
            entry.setdefault("distance", hit.get("distance"))

        fused: list[dict] = []
        for path, score in scores.items():
            row = dict(meta.get(path, {"path": path}))
            row["fused_score"] = round(score, 6)
            fused.append(row)

        fused.sort(key=lambda r: r["fused_score"], reverse=True)
        return fused

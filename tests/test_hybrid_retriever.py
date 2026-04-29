# / tests for HybridRetriever — RRF fusion of tsvector + vector search

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.knowledge.hybrid_retriever import RRF_K, HybridRetriever


def _make_retriever(
    keyword_hits: list[dict], vec_hits: list[dict],
    query_vec: list[float] | None = None,
) -> HybridRetriever:
    # / construct a retriever with the keyword + vector searches stubbed to return fixtures
    pool = MagicMock()

    wiki = MagicMock()
    wiki.search = AsyncMock(return_value=keyword_hits)

    vec = MagicMock()
    vec.search = AsyncMock(return_value=vec_hits)

    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=query_vec if query_vec is not None else [0.1] * 768)

    return HybridRetriever(pool=pool, embedder=embedder, wiki_search=wiki, vector_store=vec)


# ──────────────────────────────────────────────────────
# empty query
# ──────────────────────────────────────────────────────

class TestEmptyQuery:
    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        r = _make_retriever([], [])
        assert await r.search("") == []
        assert await r.search("   ") == []


# ──────────────────────────────────────────────────────
# RRF math
# ──────────────────────────────────────────────────────

class TestRRFMath:
    @pytest.mark.asyncio
    async def test_rrf_hand_computed_scores_and_ranking(self):
        # / hand-compute: keyword [A, B] and vector [B, C] with k=60
        # /   A: 1/(60+1) = 1/61
        # /   B: 1/(60+2) + 1/(60+1) = 1/62 + 1/61
        # /   C: 1/(60+2) = 1/62
        # / ranking: B > A > C  (B has two contributions)
        kw_hits = [
            {"path": "docs/A.md", "title": "A"},
            {"path": "docs/B.md", "title": "B"},
        ]
        vec_hits = [
            {"path": "docs/B.md", "title": "B", "chunk_text": "content B"},
            {"path": "docs/C.md", "title": "C", "chunk_text": "content C"},
        ]
        r = _make_retriever(kw_hits, vec_hits)
        results = await r.search("query", top_k=3)

        assert len(results) == 3
        by_path = {x["path"]: x for x in results}

        expected_a = 1.0 / (RRF_K + 1)
        expected_b = 1.0 / (RRF_K + 2) + 1.0 / (RRF_K + 1)
        expected_c = 1.0 / (RRF_K + 2)

        assert by_path["docs/A.md"]["fused_score"] == pytest.approx(expected_a, abs=1e-6)
        assert by_path["docs/B.md"]["fused_score"] == pytest.approx(expected_b, abs=1e-6)
        assert by_path["docs/C.md"]["fused_score"] == pytest.approx(expected_c, abs=1e-6)

        # / ordering: B highest, then A, then C
        assert results[0]["path"] == "docs/B.md"
        assert results[1]["path"] == "docs/A.md"
        assert results[2]["path"] == "docs/C.md"


# ──────────────────────────────────────────────────────
# keyword-only / vector-only / both empty
# ──────────────────────────────────────────────────────

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_keyword_only_vector_returns_empty(self):
        # / tsvector → [A] ; vector_store → [] ; result should surface A
        kw = [{"path": "a.md", "title": "A"}]
        r = _make_retriever(kw, [])
        out = await r.search("x", top_k=5)
        assert len(out) == 1
        assert out[0]["path"] == "a.md"
        # / score = 1/(60+1)
        assert out[0]["fused_score"] == pytest.approx(1.0 / (RRF_K + 1), abs=1e-6)

    @pytest.mark.asyncio
    async def test_vector_only_keyword_returns_empty(self):
        vec = [{"path": "v.md", "chunk_text": "stuff", "title": "V"}]
        r = _make_retriever([], vec)
        out = await r.search("x", top_k=5)
        assert len(out) == 1
        assert out[0]["path"] == "v.md"
        assert out[0]["fused_score"] == pytest.approx(1.0 / (RRF_K + 1), abs=1e-6)

    @pytest.mark.asyncio
    async def test_both_empty_returns_empty(self):
        r = _make_retriever([], [])
        assert await r.search("query") == []

    @pytest.mark.asyncio
    async def test_vector_embed_none_skips_vector_search(self):
        # / if embedder returns None (ollama down), vector search should not run
        kw = [{"path": "only_kw.md", "title": "K"}]
        pool = MagicMock()
        wiki = MagicMock()
        wiki.search = AsyncMock(return_value=kw)
        vec = MagicMock()
        vec.search = AsyncMock(return_value=[{"path": "should_not_appear.md"}])
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=None)
        r = HybridRetriever(pool=pool, embedder=embedder, wiki_search=wiki, vector_store=vec)

        out = await r.search("q", top_k=5)
        assert len(out) == 1
        assert out[0]["path"] == "only_kw.md"
        vec.search.assert_not_called()


# ──────────────────────────────────────────────────────
# top_k and filters
# ──────────────────────────────────────────────────────

class TestTopKAndFilters:
    @pytest.mark.asyncio
    async def test_top_k_respected(self):
        # / 6 unique paths fused, top_k=2 → 2 results
        kw = [{"path": f"p{i}.md", "title": f"P{i}"} for i in range(6)]
        r = _make_retriever(kw, [])
        out = await r.search("x", top_k=2)
        assert len(out) == 2

    @pytest.mark.asyncio
    async def test_symbols_filter_forwarded_to_both(self):
        kw_search = AsyncMock(return_value=[])
        vec_search = AsyncMock(return_value=[])
        pool = MagicMock()
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[0.1] * 768)
        wiki = MagicMock(search=kw_search)
        vec = MagicMock(search=vec_search)
        r = HybridRetriever(pool=pool, embedder=embedder, wiki_search=wiki, vector_store=vec)

        await r.search("q", top_k=3, symbols=["AAPL"])

        # / both the wiki_search and vector_store searches must have received the symbol filter
        assert kw_search.await_args.kwargs.get("symbols") == ["AAPL"]
        assert vec_search.await_args.kwargs.get("symbols") == ["AAPL"]

    @pytest.mark.asyncio
    async def test_deduplication_by_path(self):
        # / same path in both sources should produce one row, not two
        kw = [{"path": "dup.md", "title": "D"}]
        vec = [{"path": "dup.md", "title": "D", "chunk_text": "body"}]
        r = _make_retriever(kw, vec)
        out = await r.search("x", top_k=5)
        assert len(out) == 1
        assert out[0]["path"] == "dup.md"

    @pytest.mark.asyncio
    async def test_score_descending_order(self):
        # / hit paths in two lists, top_k=all → descending fused scores
        kw = [
            {"path": "p1.md", "title": "P1"},
            {"path": "p2.md", "title": "P2"},
            {"path": "p3.md", "title": "P3"},
        ]
        vec = [
            {"path": "p3.md", "title": "P3", "chunk_text": "z"},
            {"path": "p2.md", "title": "P2", "chunk_text": "y"},
        ]
        r = _make_retriever(kw, vec)
        out = await r.search("x", top_k=5)
        scores = [o["fused_score"] for o in out]
        assert scores == sorted(scores, reverse=True)

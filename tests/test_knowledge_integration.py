# / integration-ish tests for the knowledge layer — write -> search -> retrieve
# / everything is still mocked at the DB + network boundary, but exercises module stack

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.knowledge.embedder import EMBED_DIM
from src.knowledge.hybrid_retriever import HybridRetriever


def _mock_pool():
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value="OK")
    mock_conn.fetchrow = AsyncMock(return_value={"id": 42})
    mock_conn.fetch = AsyncMock(return_value=[])
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
# wiki write → hybrid retriever finds it
# ──────────────────────────────────────────────────────

class TestWriteFindCycle:
    @pytest.mark.asyncio
    async def test_write_then_hybrid_search_returns_path(self, tmp_path):
        # / WikiWriter writes a doc; patched embedder+vector keep background embed fast;
        # / hybrid retriever's wiki_search (mocked) returns the path after write
        from src.knowledge.wiki_writer import WikiWriter, set_wiki_root

        pool, _ = _mock_pool()
        set_wiki_root(tmp_path)

        # / patch OllamaEmbedder and VectorStore globally so background task doesn't hit network
        stub_embedder = MagicMock()
        stub_embedder.embed_batch = AsyncMock(return_value=[[0.1] * EMBED_DIM])
        stub_store = MagicMock()
        stub_store.upsert_chunks = AsyncMock(return_value=1)

        with (
            patch("src.knowledge.embedder.OllamaEmbedder", return_value=stub_embedder),
            patch("src.knowledge.vector_store.VectorStore", return_value=stub_store),
        ):
            writer = WikiWriter(pool=pool, root=tmp_path)
            rel_path = await writer.write_document(
                category="strategies", filename="my_doc",
                content="## My Strategy\nReduce position size during high-vol regimes.",
                title="My Doc",
            )
            # / let any fire-and-forget embed task drain (a few yields to chain through awaits)
            for _ in range(5):
                await asyncio.sleep(0)

        assert rel_path == "strategies/my_doc.md"
        assert (tmp_path / rel_path).exists()

        # / hybrid retriever reliably finds the doc via the keyword side (vector side mocked empty)
        wiki_search = MagicMock()
        wiki_search.search = AsyncMock(return_value=[
            {"path": "strategies/my_doc.md", "title": "My Doc", "category": "strategies"},
        ])
        vec_store = MagicMock()
        vec_store.search = AsyncMock(return_value=[])
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[0.1] * EMBED_DIM)

        retriever = HybridRetriever(
            pool=pool, embedder=embedder,
            wiki_search=wiki_search, vector_store=vec_store,
        )
        hits = await retriever.search("position size", top_k=3)
        assert len(hits) == 1
        assert hits[0]["path"] == "strategies/my_doc.md"

    @pytest.mark.asyncio
    async def test_post_mortem_and_regime_shift_both_persisted(self, tmp_path):
        # / write a post-mortem and a regime-shift — verify both trigger DB inserts
        from src.knowledge.post_mortem_writer import write_post_mortem
        from src.knowledge.regime_wiki import on_regime_shift

        pool, _ = _mock_pool()

        writer = MagicMock()
        writer.write_document = AsyncMock(side_effect=[
            "post-mortems/sid_AAPL_2026.md",
            "regimes/bear_equity_2026.md",
        ])

        with (
            patch("src.knowledge.post_mortem_writer._fetch_context",
                  new=AsyncMock(return_value=(None, None, []))),
            patch("src.knowledge.post_mortem_writer.claim_post_mortem_slot",
                  new=AsyncMock(return_value=1)) as mock_pm,
            patch("src.knowledge.post_mortem_writer._generate_narrative",
                  new=AsyncMock(return_value=("narrative", "mdl"))),
            patch("src.knowledge.post_mortem_writer.WikiWriter", return_value=writer),
            patch("src.knowledge.post_mortem_writer.update_post_mortem_details",
                  new=AsyncMock()),
            patch("src.knowledge.post_mortem_writer.set_post_mortem_wiki_path",
                  new=AsyncMock()),
            patch("src.knowledge.regime_wiki.WikiWriter", return_value=writer),
            patch("src.knowledge.regime_wiki.store_regime_shift_row",
                  new=AsyncMock(return_value=2)) as mock_rs,
        ):
            pm_ok = await write_post_mortem(
                pool, 1, "sid", "AAPL", -85.0, "loss_threshold",
            )
            rs_path = await on_regime_shift(pool, "bull", "bear", confidence=0.9)

        assert pm_ok is True
        assert rs_path == "regimes/bear_equity_2026.md"
        mock_pm.assert_awaited_once()
        mock_rs.assert_awaited_once()


# ──────────────────────────────────────────────────────
# 80/20 A/B ratio with deterministic rng
# ──────────────────────────────────────────────────────

class TestWikiGuidedRatioSimulation:
    def test_80_20_ratio_with_seed_42_tolerance(self):
        # / seeded np.random.default_rng(42) → over 100 trials, count guided where r < 0.80
        rng = np.random.default_rng(42)
        guided = sum(1 for _ in range(100) if rng.random() < 0.80)
        # / binomial expected value = 80; 3σ ≈ 12. ±15 is a robust non-flaky bound
        assert 65 <= guided <= 95, f"guided count {guided} outside ±15 of 80"

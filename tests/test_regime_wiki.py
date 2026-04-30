# / tests for on_regime_shift — wiki doc + regime_shifts row

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.knowledge.regime_wiki import _compose_markdown, on_regime_shift


def _mock_pool():
    mock_conn = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool, mock_conn


# ──────────────────────────────────────────────────────
# _compose_markdown
# ──────────────────────────────────────────────────────

class TestComposeMarkdown:
    def test_header_with_both_regimes(self):
        md = _compose_markdown("bull", "bear", "equity", 0.85)
        assert md.startswith("# Regime Shift: bull -> bear")
        assert "Market:** equity" in md
        assert "0.85" in md

    def test_confidence_omitted_when_none(self):
        md = _compose_markdown("bull", "bear", "equity", None)
        assert "Detector confidence" not in md

    def test_market_param_included(self):
        md = _compose_markdown("bull", "bear", "crypto", 0.5)
        assert "Market:** crypto" in md


# ──────────────────────────────────────────────────────
# on_regime_shift
# ──────────────────────────────────────────────────────

class TestOnRegimeShift:
    @pytest.mark.asyncio
    async def test_writes_doc_and_inserts_row(self):
        # / happy path: wiki write + regime_shifts row both invoked
        pool, _ = _mock_pool()
        writer = MagicMock()
        writer.write_document = AsyncMock(return_value="regimes/bear_equity_2026-04-16.md")

        with (
            patch("src.knowledge.regime_wiki.WikiWriter", return_value=writer),
            patch("src.knowledge.regime_wiki.store_regime_shift_row",
                  new=AsyncMock(return_value=10)) as mock_store,
        ):
            path = await on_regime_shift(pool, "bull", "bear", confidence=0.9, market="equity")

        assert path == "regimes/bear_equity_2026-04-16.md"
        writer.write_document.assert_awaited_once()
        wkw = writer.write_document.await_args.kwargs
        assert wkw["category"] == "regimes"
        assert wkw["filename"].startswith("bear_equity_")
        assert "bull -> bear" in wkw["content"]

        mock_store.assert_awaited_once()
        row_kwargs = mock_store.await_args.kwargs
        assert row_kwargs["old_regime"] == "bull"
        assert row_kwargs["new_regime"] == "bear"
        assert row_kwargs["market"] == "equity"
        assert row_kwargs["confidence"] == 0.9
        assert row_kwargs["wiki_path"] == "regimes/bear_equity_2026-04-16.md"

    @pytest.mark.asyncio
    async def test_same_old_and_new_skipped(self):
        # / no-op when regime didn't actually shift
        pool, _ = _mock_pool()
        with (
            patch("src.knowledge.regime_wiki.WikiWriter") as mock_writer_cls,
            patch("src.knowledge.regime_wiki.store_regime_shift_row",
                  new=AsyncMock()) as mock_store,
        ):
            path = await on_regime_shift(pool, "bull", "bull", confidence=0.5)
        assert path is None
        mock_writer_cls.assert_not_called()
        mock_store.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_regime_strings_skipped(self):
        pool, _ = _mock_pool()
        with patch("src.knowledge.regime_wiki.WikiWriter") as mock_writer_cls:
            assert await on_regime_shift(pool, "", "bear") is None
            assert await on_regime_shift(pool, "bull", "") is None
        mock_writer_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_market_defaults_to_equity(self):
        # / omitting the market kwarg must default to "equity"
        pool, _ = _mock_pool()
        writer = MagicMock()
        writer.write_document = AsyncMock(return_value="r.md")

        with (
            patch("src.knowledge.regime_wiki.WikiWriter", return_value=writer),
            patch("src.knowledge.regime_wiki.store_regime_shift_row",
                  new=AsyncMock()) as mock_store,
        ):
            await on_regime_shift(pool, "bull", "bear")

        assert mock_store.await_args.kwargs["market"] == "equity"

    @pytest.mark.asyncio
    async def test_crypto_market_passed_through(self):
        pool, _ = _mock_pool()
        writer = MagicMock()
        writer.write_document = AsyncMock(return_value="r.md")

        with (
            patch("src.knowledge.regime_wiki.WikiWriter", return_value=writer),
            patch("src.knowledge.regime_wiki.store_regime_shift_row",
                  new=AsyncMock()) as mock_store,
        ):
            await on_regime_shift(pool, "sideways", "bull", market="crypto")

        assert mock_store.await_args.kwargs["market"] == "crypto"

    @pytest.mark.asyncio
    async def test_confidence_optional_no_crash(self):
        # / confidence=None must still produce a row with confidence=None
        pool, _ = _mock_pool()
        writer = MagicMock()
        writer.write_document = AsyncMock(return_value="r.md")

        with (
            patch("src.knowledge.regime_wiki.WikiWriter", return_value=writer),
            patch("src.knowledge.regime_wiki.store_regime_shift_row",
                  new=AsyncMock()) as mock_store,
        ):
            await on_regime_shift(pool, "bull", "bear", confidence=None)

        assert mock_store.await_args.kwargs["confidence"] is None

    @pytest.mark.asyncio
    async def test_wiki_write_failure_still_inserts_row(self):
        # / wiki write fails → still try to insert row with wiki_path=None
        pool, _ = _mock_pool()
        writer = MagicMock()
        writer.write_document = AsyncMock(side_effect=OSError("disk"))

        with (
            patch("src.knowledge.regime_wiki.WikiWriter", return_value=writer),
            patch("src.knowledge.regime_wiki.store_regime_shift_row",
                  new=AsyncMock(return_value=1)) as mock_store,
        ):
            path = await on_regime_shift(pool, "bull", "bear", confidence=0.5)

        # / wiki_path is None when the write failed
        assert mock_store.await_args.kwargs["wiki_path"] is None
        assert path is None

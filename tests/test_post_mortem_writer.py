# / tests for write_post_mortem — cooldown gate, llm chain, template fallback, db insert

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analysis.ai_summary import AnalysisSummary
from src.knowledge.post_mortem_writer import (
    _build_prompt,
    _compose_markdown,
    _template_narrative,
    write_post_mortem,
)


def _mock_pool():
    mock_conn = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool, mock_conn


def _sample_summary(text: str = "Narrative body") -> AnalysisSummary:
    from datetime import date
    return AnalysisSummary(
        symbol="AAPL", date=date.today(), summary=text,
        model_used="llama-3.3-70b", signal="neutral", confidence=70.0,
    )


# ──────────────────────────────────────────────────────
# _build_prompt
# ──────────────────────────────────────────────────────

class TestBuildPrompt:
    def test_includes_core_fields(self):
        p = _build_prompt(
            symbol="AAPL", strategy_id="sid_x", pnl=-123.45,
            trigger_type="loss_threshold", deviation_sigma=1.5,
            trade={"side": "sell", "qty": 10, "price": 180.0},
            strategy_config=None, recent_trades=None,
        )
        assert "AAPL" in p
        assert "sid_x" in p
        assert "-123.45" in p
        assert "loss_threshold" in p
        assert "1.50" in p

    def test_recent_trades_truncated_to_8(self):
        trades = [{"symbol": "X", "side": "buy", "pnl": -i, "created_at": "2026-01-01"} for i in range(20)]
        p = _build_prompt(
            symbol="X", strategy_id="s", pnl=-10.0, trigger_type="x",
            deviation_sigma=None, trade=None, strategy_config=None,
            recent_trades=trades,
        )
        # / first 8 should be visible, the 9th (pnl=-8) should NOT
        assert "pnl=-7" in p
        assert "pnl=-8" not in p

    def test_no_optional_fields(self):
        # / minimal prompt still produces a usable template
        p = _build_prompt(
            symbol="X", strategy_id="s", pnl=-5.0, trigger_type="x",
            deviation_sigma=None, trade=None, strategy_config=None,
            recent_trades=None,
        )
        assert "X" in p
        assert "Task" in p

    def test_loss_prompt_says_loss_and_diagnose(self):
        p = _build_prompt(
            symbol="X", strategy_id="s", pnl=-50.0,
            trigger_type="loss_threshold", deviation_sigma=None,
            trade=None, strategy_config=None, recent_trades=None,
        )
        assert "loss" in p.lower()
        assert "Diagnose" in p

    def test_win_prompt_says_win_and_preserve(self):
        p = _build_prompt(
            symbol="X", strategy_id="s", pnl=120.0,
            trigger_type="win_threshold", deviation_sigma=None,
            trade=None, strategy_config=None, recent_trades=None,
        )
        assert "win" in p.lower()
        assert "preserve" in p.lower()


# ──────────────────────────────────────────────────────
# _compose_markdown
# ──────────────────────────────────────────────────────

class TestComposeMarkdown:
    def test_required_header_fields(self):
        md = _compose_markdown(
            symbol="AAPL", strategy_id="sid", pnl=-100.5,
            trigger_type="loss_threshold", deviation_sigma=None,
            narrative="Body here.", model_used="m_test", trade=None,
        )
        assert md.startswith("# Post-Mortem (Loss): sid on AAPL")
        assert "-100.50" in md
        assert "loss_threshold" in md
        assert "m_test" in md
        assert "Body here." in md

    def test_win_header_renders_post_analysis(self):
        md = _compose_markdown(
            symbol="AAPL", strategy_id="sid", pnl=200.5,
            trigger_type="win_threshold", deviation_sigma=None,
            narrative="Body.", model_used="m", trade=None,
        )
        assert md.startswith("# Post-Analysis (Win): sid on AAPL")
        assert "win_threshold" in md

    def test_template_fallback_when_no_model(self):
        md = _compose_markdown(
            symbol="X", strategy_id="s", pnl=-1.0, trigger_type="x",
            deviation_sigma=None, narrative="t", model_used=None, trade=None,
        )
        assert "template fallback" in md

    def test_trade_details_rendered(self):
        md = _compose_markdown(
            symbol="X", strategy_id="s", pnl=-1.0, trigger_type="x",
            deviation_sigma=None, narrative="n", model_used="m",
            trade={"side": "sell", "qty": 5, "price": 12.3, "broker": "PaperBroker"},
        )
        assert "side: sell" in md
        assert "broker: PaperBroker" in md


# ──────────────────────────────────────────────────────
# _template_narrative
# ──────────────────────────────────────────────────────

def test_template_narrative_contains_inputs():
    text = _template_narrative("AAPL", "sid_x", -45.67, "loss_threshold")
    assert "AAPL" in text
    assert "sid_x" in text
    assert "-45.67" in text
    assert "loss_threshold" in text


# ──────────────────────────────────────────────────────
# write_post_mortem — full integration with mocks
# ──────────────────────────────────────────────────────

class TestWritePostMortemEndToEnd:
    @pytest.mark.asyncio
    async def test_empty_identifiers_returns_false(self):
        # / guard against missing strategy_id or symbol
        pool, _ = _mock_pool()
        assert await write_post_mortem(pool, 1, "", "AAPL", -50.0, "loss_threshold") is False
        assert await write_post_mortem(pool, 1, "sid", "", -50.0, "loss_threshold") is False

    @pytest.mark.asyncio
    async def test_cooldown_blocks_second_write(self):
        # / claim_post_mortem_slot returns None on cooldown → writer returns False early
        pool, _ = _mock_pool()

        with (
            patch("src.knowledge.post_mortem_writer._fetch_context",
                  new=AsyncMock(return_value=(None, None, []))),
            patch("src.knowledge.post_mortem_writer.claim_post_mortem_slot",
                  new=AsyncMock(return_value=None)) as mock_claim,
            patch("src.knowledge.post_mortem_writer.WikiWriter") as mock_writer_cls,
            patch("src.knowledge.post_mortem_writer.set_post_mortem_wiki_path",
                  new=AsyncMock()) as mock_set_path,
        ):
            ok = await write_post_mortem(
                pool, trade_id=1, strategy_id="sid",
                symbol="AAPL", pnl=-100.0, trigger_type="loss_threshold",
            )

        assert ok is False
        mock_claim.assert_awaited_once()
        mock_writer_cls.assert_not_called()
        mock_set_path.assert_not_called()

    @pytest.mark.asyncio
    async def test_happy_path_groq_chain_success(self):
        # / cooldown OK → groq narrative works → wiki write + row insert both happen
        pool, _ = _mock_pool()

        mock_writer = MagicMock()
        mock_writer.write_document = AsyncMock(return_value="post-mortems/sid_AAPL_2026-04-16.md")

        with (
            patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}),
            patch("src.knowledge.post_mortem_writer._fetch_context",
                  new=AsyncMock(return_value=(None, None, []))),
            patch("src.knowledge.post_mortem_writer.claim_post_mortem_slot",
                  new=AsyncMock(return_value=101)) as mock_claim,
            patch("src.knowledge.post_mortem_writer._generate_narrative",
                  new=AsyncMock(return_value=("GROQ OUTPUT", "llama-3.3-70b"))),
            patch("src.knowledge.post_mortem_writer.WikiWriter", return_value=mock_writer),
            patch("src.knowledge.post_mortem_writer.update_post_mortem_details",
                  new=AsyncMock()) as mock_update_details,
            patch("src.knowledge.post_mortem_writer.set_post_mortem_wiki_path",
                  new=AsyncMock()) as mock_set_path,
        ):
            ok = await write_post_mortem(
                pool, trade_id=5, strategy_id="sid",
                symbol="AAPL", pnl=-123.0, trigger_type="loss_threshold",
            )

        assert ok is True
        # / wiki document written with the correct category and filename stem
        mock_writer.write_document.assert_awaited_once()
        kwargs = mock_writer.write_document.await_args.kwargs
        assert kwargs["category"] == "post-mortems"
        assert "sid" in kwargs["filename"]
        assert "AAPL" in kwargs["filename"]
        # / groq narrative appears in the body
        assert "GROQ OUTPUT" in kwargs["content"]

        # / post_mortems row claimed atomically, then wiki path patched after write
        mock_claim.assert_awaited_once()
        claim_kwargs = mock_claim.await_args.kwargs
        assert claim_kwargs["strategy_id"] == "sid"
        assert claim_kwargs["symbol"] == "AAPL"
        assert claim_kwargs["pnl"] == -123.0
        assert claim_kwargs["trigger_type"] == "loss_threshold"
        mock_update_details.assert_awaited_once()
        mock_set_path.assert_awaited_once()
        set_path_args = mock_set_path.await_args.args
        assert set_path_args[1] == 101  # / row_id forwarded
        assert set_path_args[2] == "post-mortems/sid_AAPL_2026-04-16.md"

    @pytest.mark.asyncio
    async def test_llm_chain_failure_uses_template(self):
        # / _generate_narrative returns (None, None) → template_narrative used, still writes
        pool, _ = _mock_pool()
        mock_writer = MagicMock()
        mock_writer.write_document = AsyncMock(return_value="post-mortems/p.md")

        with (
            patch("src.knowledge.post_mortem_writer._fetch_context",
                  new=AsyncMock(return_value=(None, None, []))),
            patch("src.knowledge.post_mortem_writer.claim_post_mortem_slot",
                  new=AsyncMock(return_value=102)),
            patch("src.knowledge.post_mortem_writer._generate_narrative",
                  new=AsyncMock(return_value=(None, None))),
            patch("src.knowledge.post_mortem_writer.WikiWriter", return_value=mock_writer),
            patch("src.knowledge.post_mortem_writer.update_post_mortem_details",
                  new=AsyncMock()),
            patch("src.knowledge.post_mortem_writer.set_post_mortem_wiki_path",
                  new=AsyncMock()),
        ):
            ok = await write_post_mortem(
                pool, 1, "sid", "AAPL", -90.0, "loss_threshold",
            )

        assert ok is True
        kwargs = mock_writer.write_document.await_args.kwargs
        # / template fallback text present in markdown body
        assert "LLM narrative unavailable" in kwargs["content"]

    @pytest.mark.asyncio
    async def test_wiki_write_failure_still_inserts_row(self):
        # / if the wiki file write throws, row should still land (with wiki_path=None)
        pool, _ = _mock_pool()
        mock_writer = MagicMock()
        mock_writer.write_document = AsyncMock(side_effect=OSError("disk full"))

        with (
            patch("src.knowledge.post_mortem_writer._fetch_context",
                  new=AsyncMock(return_value=(None, None, []))),
            patch("src.knowledge.post_mortem_writer.claim_post_mortem_slot",
                  new=AsyncMock(return_value=42)) as mock_claim,
            patch("src.knowledge.post_mortem_writer._generate_narrative",
                  new=AsyncMock(return_value=(None, None))),
            patch("src.knowledge.post_mortem_writer.WikiWriter", return_value=mock_writer),
            patch("src.knowledge.post_mortem_writer.update_post_mortem_details",
                  new=AsyncMock()),
            patch("src.knowledge.post_mortem_writer.set_post_mortem_wiki_path",
                  new=AsyncMock()) as mock_set_path,
        ):
            ok = await write_post_mortem(pool, 1, "sid", "AAPL", -80.0, "loss_threshold")

        # / row was claimed atomically; wiki write failed → set_wiki_path called with None
        mock_claim.assert_awaited_once()
        mock_set_path.assert_awaited_once()
        assert mock_set_path.await_args.args[2] is None
        # / function returns True (row inserted) even though wiki failed
        assert ok is True

    @pytest.mark.asyncio
    async def test_strategy_config_not_found_still_succeeds(self):
        # / missing config should not prevent the post-mortem — uses minimal context
        pool, _ = _mock_pool()
        mock_writer = MagicMock()
        mock_writer.write_document = AsyncMock(return_value="post-mortems/z.md")

        with (
            patch("src.knowledge.post_mortem_writer._fetch_context",
                  new=AsyncMock(return_value=(None, None, []))),  # / strategy_config=None
            patch("src.knowledge.post_mortem_writer.claim_post_mortem_slot",
                  new=AsyncMock(return_value=1)) as mock_claim,
            patch("src.knowledge.post_mortem_writer._generate_narrative",
                  new=AsyncMock(return_value=("n", "m"))),
            patch("src.knowledge.post_mortem_writer.WikiWriter", return_value=mock_writer),
            patch("src.knowledge.post_mortem_writer.update_post_mortem_details",
                  new=AsyncMock()),
            patch("src.knowledge.post_mortem_writer.set_post_mortem_wiki_path",
                  new=AsyncMock()),
        ):
            ok = await write_post_mortem(pool, 1, "s", "A", -60.0, "loss_threshold")

        assert ok is True
        details = mock_claim.await_args.kwargs["details"]
        assert details["has_strategy_config"] is False

    @pytest.mark.asyncio
    async def test_trigger_type_propagates_to_row(self):
        pool, _ = _mock_pool()
        mock_writer = MagicMock()
        mock_writer.write_document = AsyncMock(return_value="p.md")

        with (
            patch("src.knowledge.post_mortem_writer._fetch_context",
                  new=AsyncMock(return_value=(None, None, []))),
            patch("src.knowledge.post_mortem_writer.claim_post_mortem_slot",
                  new=AsyncMock(return_value=1)) as mock_claim,
            patch("src.knowledge.post_mortem_writer._generate_narrative",
                  new=AsyncMock(return_value=("n", "m"))),
            patch("src.knowledge.post_mortem_writer.WikiWriter", return_value=mock_writer),
            patch("src.knowledge.post_mortem_writer.update_post_mortem_details",
                  new=AsyncMock()),
            patch("src.knowledge.post_mortem_writer.set_post_mortem_wiki_path",
                  new=AsyncMock()),
        ):
            await write_post_mortem(pool, 1, "s", "A", -60.0, "custom_trigger_x")

        assert mock_claim.await_args.kwargs["trigger_type"] == "custom_trigger_x"

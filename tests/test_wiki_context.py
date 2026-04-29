# / tests for WikiContext — assembles wiki snippets for llm prompts under a token budget

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.knowledge.wiki_context import (
    CHARS_PER_TOKEN,
    DEFAULT_CONTEXT_BUDGET,
    WikiContext,
    _estimate_tokens,
    _truncate_to_tokens,
)


def _make_context(
    search_results: dict | None = None,
    read_results: dict | None = None,
    list_results: list[dict] | None = None,
) -> WikiContext:
    # / stub writer + retriever with canned responses
    pool = MagicMock()

    writer = MagicMock()
    writer.read_document = AsyncMock(
        side_effect=lambda path: (read_results or {}).get(path),
    )
    writer.list_documents = AsyncMock(return_value=list_results or [])

    retriever = MagicMock()
    lookup = search_results or {}

    def _search(*args, **kwargs):
        category = kwargs.get("category")
        if category is None:
            return lookup.get(("search", None), [])
        # / category-keyed lookup matches both by_category and search+category
        return lookup.get(("by_category", category)) or lookup.get(("search", category), [])

    retriever.search = AsyncMock(side_effect=_search)

    return WikiContext(pool=pool, writer=writer, retriever=retriever)


# ──────────────────────────────────────────────────────
# pure helper functions
# ──────────────────────────────────────────────────────

class TestEstimateTokens:
    def test_chars_per_token_formula(self):
        # / len(text) // 4 with floor at 1
        assert _estimate_tokens("a" * 400) == 100
        assert _estimate_tokens("a" * 8) == 2

    def test_short_string_floors_at_1(self):
        assert _estimate_tokens("a") == 1
        assert _estimate_tokens("") == 1


class TestTruncateToTokens:
    def test_short_text_untouched(self):
        text = "short content"
        assert _truncate_to_tokens(text, 100) == text

    def test_exact_boundary_no_truncation(self):
        # / max_chars = 10 * 4 = 40
        text = "a" * 40
        assert _truncate_to_tokens(text, 10) == text

    def test_over_budget_truncates(self):
        text = "a" * 200  # / 200 chars = ~50 tokens
        out = _truncate_to_tokens(text, 10)  # / 10 tokens = 40 chars budget
        assert len(out) <= 40 + 3  # / trailing "..." ok

    def test_sentence_boundary_preferred_when_past_60_pct(self):
        # / max_tokens=25 -> max_chars=100. 80 a's + ". " + b's
        # / cut is first 100 chars, rfind(". ")=80, 80 > 60 (60% of 100) -> keep [:81]
        # / so result is "a"*80 + "." (just the period, not the trailing space)
        text = "a" * 80 + ". " + "b" * 100
        out = _truncate_to_tokens(text, 25)
        assert out.endswith(".")
        assert not out.endswith("...")
        assert len(out) == 81

    def test_ellipsis_when_no_sentence_break(self):
        # / text with no sentence break at all -> ellipsis
        text = "a" * 200
        out = _truncate_to_tokens(text, 10)
        assert out.endswith("...")


# ──────────────────────────────────────────────────────
# WikiContext.get_mutation_context
# ──────────────────────────────────────────────────────

class TestGetMutationContextEmpty:
    @pytest.mark.asyncio
    async def test_no_docs_returns_empty_string(self):
        ctx = _make_context()
        result = await ctx.get_mutation_context(strategy_id="sid_1")
        assert result == ""

    @pytest.mark.asyncio
    async def test_no_regime_does_not_call_regime_fetch(self):
        ctx = _make_context()
        await ctx.get_mutation_context(strategy_id="sid_1", regime=None)
        calls = [c for c in ctx._retriever.search.await_args_list
                 if c.kwargs.get("category") == "regimes"]
        assert calls == []


class TestGetMutationContextHappyPath:
    @pytest.mark.asyncio
    async def test_playbook_injected_when_available(self):
        ctx = _make_context(
            search_results={
                ("by_category", "strategies"): [{"path": "strategies/sid_1.md"}],
            },
            read_results={"strategies/sid_1.md": "Playbook: go long on dips."},
        )
        result = await ctx.get_mutation_context(strategy_id="sid_1")
        assert "STRATEGY PLAYBOOK" in result
        assert "go long on dips" in result

    @pytest.mark.asyncio
    async def test_all_sections_present_when_available(self):
        ctx = _make_context(
            search_results={
                ("by_category", "strategies"): [{"path": "strategies/sid_1.md"}],
                ("by_category", "evolution"): [{"path": "evolution/latest.md"}],
                ("by_category", "meta"): [{"path": "meta/biases.md"}],
                ("search", "regimes"): [{"path": "regimes/bull.md"}],
            },
            read_results={
                "strategies/sid_1.md": "playbook body",
                "evolution/latest.md": "evolution body",
                "meta/biases.md": "known biases body",
                "regimes/bull.md": "bull market body",
            },
        )
        result = await ctx.get_mutation_context(strategy_id="sid_1", regime="bull")
        assert "STRATEGY PLAYBOOK" in result
        assert "LAST EVOLUTION CYCLE" in result
        assert "CURRENT REGIME: BULL" in result
        assert "KNOWN SYSTEM BIASES" in result


# ──────────────────────────────────────────────────────
# WikiContext.get_analysis_context
# ──────────────────────────────────────────────────────

class TestGetAnalysisContextEmpty:
    @pytest.mark.asyncio
    async def test_no_data_returns_empty_string(self):
        ctx = _make_context()
        result = await ctx.get_analysis_context(symbol="AAPL")
        assert result == ""


class TestGetAnalysisContextHappyPath:
    @pytest.mark.asyncio
    async def test_symbol_profile_injected_when_listed(self):
        ctx = _make_context(
            list_results=[{"path": "symbols/aapl.md"}],
            read_results={"symbols/aapl.md": "AAPL is a large cap tech name."},
        )
        result = await ctx.get_analysis_context(symbol="AAPL")
        assert "AAPL PROFILE" in result
        assert "large cap tech" in result

    @pytest.mark.asyncio
    async def test_post_mortems_summarized_as_title_bullets(self):
        # / two post-mortem hits -> rendered as "- title" list
        ctx = _make_context(
            search_results={
                ("search", None): [
                    {"path": "post-mortems/x.md", "title": "AAPL October Loss"},
                    {"path": "post-mortems/y.md", "title": "AAPL Earnings Miss"},
                ],
            },
        )
        result = await ctx.get_analysis_context(symbol="AAPL")
        assert "RELEVANT POST-MORTEMS" in result
        assert "- AAPL October Loss" in result
        assert "- AAPL Earnings Miss" in result

    @pytest.mark.asyncio
    async def test_regime_section_when_regime_given(self):
        ctx = _make_context(
            search_results={
                ("search", "regimes"): [{"path": "regimes/bull.md"}],
            },
            read_results={"regimes/bull.md": "regime notes"},
        )
        result = await ctx.get_analysis_context(symbol="AAPL", regime="bull")
        assert "REGIME: BULL" in result
        assert "regime notes" in result


# ──────────────────────────────────────────────────────
# _assemble — budget + scaling + formatting
# ──────────────────────────────────────────────────────

class TestAssemble:
    def test_empty_sections_returns_empty_string(self):
        ctx = _make_context()
        assert ctx._assemble([], budget=500) == ""

    def test_under_budget_preserves_all_sections(self):
        ctx = _make_context()
        sections = [
            ("A", "alpha content", 50),
            ("B", "beta content", 50),
        ]
        out = ctx._assemble(sections, budget=DEFAULT_CONTEXT_BUDGET)
        assert "## A" in out
        assert "## B" in out
        assert "alpha content" in out
        assert "beta content" in out

    def test_heading_format_is_double_hash(self):
        ctx = _make_context()
        out = ctx._assemble([("HEADER", "body", 50)], budget=500)
        assert out.startswith("## HEADER\n")

    def test_sections_joined_by_blank_line(self):
        ctx = _make_context()
        out = ctx._assemble([("A", "x", 50), ("B", "y", 50)], budget=500)
        assert "\n\n" in out

    def test_over_budget_scales_down_allocations(self):
        # / total requested 600 > budget 100 -> proportional scaling with floor of 50
        ctx = _make_context()
        long_body = "a " * 500  # / 1000 chars, ~250 tokens
        sections = [
            ("ONE", long_body, 300),
            ("TWO", long_body, 300),
        ]
        out = ctx._assemble(sections, budget=100)
        # / result should fit inside hard cap of 100 tokens ~= 400 chars (plus ellipsis)
        assert _estimate_tokens(out) <= 100 + 5

    def test_hard_cap_enforced_on_total(self):
        ctx = _make_context()
        long_body = "x" * 5000
        sections = [("BIG", long_body, 500)]
        out = ctx._assemble(sections, budget=50)
        # / even with a single section, the hard cap must apply
        # / 50 tokens * 4 chars = 200 chars budget
        assert len(out) <= 200 + 20  # / heading + ellipsis overhead


class TestSummarizeHits:
    def test_uses_title_when_present(self):
        ctx = _make_context()
        hits = [{"path": "x.md", "title": "Alpha Lesson"}]
        out = ctx._summarize_hits(hits)
        assert out == "- Alpha Lesson"

    def test_falls_back_to_path_when_no_title(self):
        ctx = _make_context()
        hits = [{"path": "post-mortems/xyz.md"}]
        out = ctx._summarize_hits(hits)
        assert out == "- post-mortems/xyz.md"

    def test_empty_hits_returns_empty_string(self):
        ctx = _make_context()
        assert ctx._summarize_hits([]) == ""


# ──────────────────────────────────────────────────────
# constants sanity
# ──────────────────────────────────────────────────────

class TestConstants:
    def test_chars_per_token_is_4(self):
        assert CHARS_PER_TOKEN == 4

    def test_default_budget_positive(self):
        assert DEFAULT_CONTEXT_BUDGET > 0

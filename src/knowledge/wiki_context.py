# / assembles wiki context for llm prompts with strict token budget
# / used by evolution engine (mutation prompts) + analyst agent (analysis prompts)

from __future__ import annotations

import structlog

from src.knowledge.wiki_search import WikiSearch
from src.knowledge.wiki_writer import WikiWriter

logger = structlog.get_logger(__name__)

# / max tokens injected into any single llm prompt from wiki
DEFAULT_CONTEXT_BUDGET = 750
# / rough token estimate: ~4 chars per token
CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    # / preserves full sentences when possible
    max_chars = max_tokens * CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    last_sentence = cut.rfind(". ")
    if last_sentence > max_chars * 0.6:
        return cut[:last_sentence + 1]
    return cut.rstrip() + "..."


class WikiContext:
    # / fetches relevant wiki snippets and formats them for llm injection

    def __init__(self, pool, writer: WikiWriter | None = None, search: WikiSearch | None = None):
        self._pool = pool
        self._writer = writer or WikiWriter(pool=pool)
        self._search = search or WikiSearch(pool=pool)

    async def get_mutation_context(
        self,
        strategy_id: str,
        killed_config: dict | None = None,
        top_config: dict | None = None,
        regime: str | None = None,
        budget: int = DEFAULT_CONTEXT_BUDGET,
    ) -> str:
        # / assembles context for strategy mutation prompts
        # / sources: strategy playbook, last evolution report, current regime, meta known-issues
        sections: list[tuple[str, str, int]] = []

        playbook = await self._fetch_latest(category="strategies", strategy_id=strategy_id)
        if playbook:
            sections.append(("STRATEGY PLAYBOOK", playbook, 300))

        evolution_doc = await self._fetch_latest(category="evolution", strategy_id=strategy_id)
        if evolution_doc:
            sections.append(("LAST EVOLUTION CYCLE", evolution_doc, 200))

        if regime:
            regime_doc = await self._fetch_regime(regime)
            if regime_doc:
                sections.append((f"CURRENT REGIME: {regime.upper()}", regime_doc, 150))

        meta_issues = await self._fetch_meta_known_issues()
        if meta_issues:
            sections.append(("KNOWN SYSTEM BIASES", meta_issues, 100))

        return self._assemble(sections, budget)

    async def get_analysis_context(
        self,
        symbol: str,
        regime: str | None = None,
        budget: int = DEFAULT_CONTEXT_BUDGET,
    ) -> str:
        # / assembles context for analyst-agent prompts
        # / sources: symbol profile, relevant post-mortems, current regime notes
        sections: list[tuple[str, str, int]] = []

        profile = await self._fetch_symbol_profile(symbol)
        if profile:
            sections.append((f"{symbol} PROFILE", profile, 250))

        post_mortems = await self._search.search(
            f"{symbol} lessons loss", symbols=[symbol], top_k=2,
        )
        if post_mortems:
            text = self._summarize_hits(post_mortems)
            sections.append(("RELEVANT POST-MORTEMS", text, 250))

        if regime:
            regime_doc = await self._fetch_regime(regime)
            if regime_doc:
                sections.append((f"REGIME: {regime.upper()}", regime_doc, 150))

        return self._assemble(sections, budget)

    async def _fetch_latest(self, category: str, strategy_id: str) -> str | None:
        results = await self._search.search_by_category(
            category=category, query=strategy_id, top_k=1,
        )
        if not results:
            return None
        content = await self._writer.read_document(results[0]["path"])
        return content

    async def _fetch_regime(self, regime: str) -> str | None:
        results = await self._search.search(
            query=regime, category="regimes", top_k=1,
        )
        if not results:
            return None
        return await self._writer.read_document(results[0]["path"])

    async def _fetch_meta_known_issues(self) -> str | None:
        results = await self._search.search_by_category(
            category="meta", query="known issues biases", top_k=1,
        )
        if not results:
            return None
        return await self._writer.read_document(results[0]["path"])

    async def _fetch_symbol_profile(self, symbol: str) -> str | None:
        docs = await self._writer.list_documents(category="symbols", symbols=[symbol], limit=1)
        if not docs:
            return None
        return await self._writer.read_document(docs[0]["path"])

    def _summarize_hits(self, hits: list[dict]) -> str:
        lines: list[str] = []
        for hit in hits:
            title = hit.get("title") or hit["path"]
            lines.append(f"- {title}")
        return "\n".join(lines)

    def _assemble(self, sections: list[tuple[str, str, int]], budget: int) -> str:
        if not sections:
            return ""
        total_requested = sum(s[2] for s in sections)
        if total_requested <= budget:
            allocations = [s[2] for s in sections]
        else:
            # / scale down proportionally
            scale = budget / total_requested
            allocations = [max(50, int(s[2] * scale)) for s in sections]

        out_parts: list[str] = []
        remaining = budget
        for (heading, content, _req), alloc in zip(sections, allocations, strict=False):
            alloc = min(alloc, remaining)
            if alloc <= 20:
                break
            body = _truncate_to_tokens(content, alloc)
            out_parts.append(f"## {heading}\n{body}")
            remaining -= _estimate_tokens(body) + 10  # / heading overhead

        if not out_parts:
            return ""
        assembled = "\n\n".join(out_parts)
        # / hard cap enforcement
        final = _truncate_to_tokens(assembled, budget)
        logger.debug("wiki_context_assembled", sections=len(out_parts), tokens=_estimate_tokens(final))
        return final

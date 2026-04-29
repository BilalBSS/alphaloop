
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
import structlog

logger = structlog.get_logger(__name__)

VALID_CATEGORIES = {
    "regimes", "post-mortems", "strategies", "evolution", "symbols", "meta", "archive",
}

_WIKI_ROOT: Path | None = None
_PATH_LOCKS: dict[str, asyncio.Lock] = {}
_LOCKS_GUARD = asyncio.Lock()
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def get_wiki_root() -> Path:
    global _WIKI_ROOT
    if _WIKI_ROOT is None:
        _WIKI_ROOT = Path(__file__).resolve().parents[2] / "trading-wiki"
    return _WIKI_ROOT


def set_wiki_root(path: Path | str) -> None:
    # / override for tests
    global _WIKI_ROOT
    _WIKI_ROOT = Path(path)


async def _lock_for(path: str) -> asyncio.Lock:
    async with _LOCKS_GUARD:
        if path not in _PATH_LOCKS:
            _PATH_LOCKS[path] = asyncio.Lock()
        return _PATH_LOCKS[path]


def _slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_-]+", "_", s).strip("_")
    return s[:80] or "untitled"


def _validate_category(category: str) -> None:
    if category not in VALID_CATEGORIES:
        raise ValueError(f"invalid wiki category: {category!r}, must be one of {sorted(VALID_CATEGORIES)}")


def _count_words(text: str) -> int:
    return len(re.findall(r"\S+", text))


class WikiWriter:

    def __init__(self, pool=None, root: Path | None = None):
        self._pool = pool
        self._root = root or get_wiki_root()

    @property
    def root(self) -> Path:
        return self._root

    def _abs_path(self, rel_path: str) -> Path:
        return self._root / rel_path

    async def write_document(
        self,
        category: str,
        filename: str,
        content: str,
        title: str | None = None,
        symbols: list[str] | None = None,
        strategy_ids: list[str] | None = None,
        confidence: str = "emerging",
    ) -> str:
        _validate_category(category)
        stem = filename[:-3] if filename.endswith(".md") else filename
        safe_stem = _slugify(stem)
        rel_path = f"{category}/{safe_stem}.md"
        abs_path = self._abs_path(rel_path)
        try:
            resolved = abs_path.resolve()
            root_resolved = self._root.resolve()
            if not str(resolved).startswith(str(root_resolved)):
                raise ValueError(f"wiki path escape: {rel_path!r}")
        except (OSError, ValueError) as exc:
            logger.error("wiki_write_path_rejected", rel_path=rel_path, error=str(exc)[:120])
            raise

        document_id: int | None = None
        lock = await _lock_for(rel_path)
        async with lock:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(abs_path, "w", encoding="utf-8") as f:
                await f.write(content)

            if self._pool is not None:
                document_id = await self._register(
                    rel_path, category, title, symbols or [], strategy_ids or [],
                    _count_words(content), confidence, content,
                )
        logger.info("wiki_document_written", path=rel_path, words=_count_words(content))

        if document_id is not None:
            try:
                task = asyncio.create_task(self._embed_async(document_id, content))
                _BACKGROUND_TASKS.add(task)
                task.add_done_callback(_BACKGROUND_TASKS.discard)
            except RuntimeError:
                pass
        return rel_path

    async def append_section(
        self,
        rel_path: str,
        heading: str,
        body: str,
    ) -> None:
        abs_path = self._abs_path(rel_path)
        lock = await _lock_for(rel_path)
        async with lock:
            if not abs_path.exists():
                raise FileNotFoundError(f"cannot append to missing doc: {rel_path}")
            async with aiofiles.open(abs_path, encoding="utf-8") as f:
                existing = await f.read()
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            section = f"\n\n## {heading} ({timestamp})\n\n{body}\n"
            updated = existing.rstrip() + section
            async with aiofiles.open(abs_path, "w", encoding="utf-8") as f:
                await f.write(updated)

            if self._pool is not None:
                await self._touch(rel_path, _count_words(updated), updated)
        logger.info("wiki_section_appended", path=rel_path, heading=heading)

    async def read_document(self, rel_path: str) -> str | None:
        abs_path = self._abs_path(rel_path)
        if not abs_path.exists():
            return None
        async with aiofiles.open(abs_path, encoding="utf-8") as f:
            return await f.read()

    async def list_documents(
        self,
        category: str | None = None,
        symbols: list[str] | None = None,
        strategy_ids: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict]:
        if self._pool is None:
            return []
        clauses: list[str] = []
        params: list = []
        if category is not None:
            _validate_category(category)
            params.append(category)
            clauses.append(f"category = ${len(params)}")
        if symbols:
            params.append(symbols)
            clauses.append(f"symbols && ${len(params)}")
        if strategy_ids:
            params.append(strategy_ids)
            clauses.append(f"strategy_ids && ${len(params)}")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        sql = f"""
            SELECT id, path, category, title, symbols, strategy_ids, word_count,
                   confidence, created_at, updated_at
            FROM wiki_documents
            {where}
            ORDER BY updated_at DESC
            LIMIT ${len(params)}
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]

    async def archive_old(self, older_than_days: int = 180) -> int:
        if self._pool is None:
            return 0
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, path FROM wiki_documents
                WHERE updated_at < NOW() - ($1::int * INTERVAL '1 day')
                  AND category <> 'archive'
                """,
                older_than_days,
            )
        moved = 0
        for row in rows:
            old_rel = row["path"]
            filename = old_rel.rsplit("/", 1)[-1]
            new_rel = f"archive/{filename}"
            abs_old = self._abs_path(old_rel)
            abs_new = self._abs_path(new_rel)
            if not abs_old.exists():
                continue
            abs_new.parent.mkdir(parents=True, exist_ok=True)
            abs_old.rename(abs_new)
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE wiki_documents SET path = $1, category = 'archive', updated_at = NOW() WHERE id = $2",
                    new_rel, row["id"],
                )
            moved += 1
        if moved:
            logger.info("wiki_archived", count=moved, older_than_days=older_than_days)
        return moved

    async def _register(
        self,
        path: str,
        category: str,
        title: str | None,
        symbols: list[str],
        strategy_ids: list[str],
        word_count: int,
        confidence: str,
        content: str,
    ) -> int | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO wiki_documents (path, category, title, symbols, strategy_ids,
                    word_count, confidence, content_tsv, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, to_tsvector('english', $8), NOW(), NOW())
                ON CONFLICT (path) DO UPDATE SET
                    category = EXCLUDED.category,
                    title = EXCLUDED.title,
                    symbols = EXCLUDED.symbols,
                    strategy_ids = EXCLUDED.strategy_ids,
                    word_count = EXCLUDED.word_count,
                    confidence = EXCLUDED.confidence,
                    content_tsv = to_tsvector('english', $8),
                    updated_at = NOW()
                RETURNING id
                """,
                path, category, title, symbols, strategy_ids, word_count, confidence, content,
            )
        return int(row["id"]) if row else None

    async def _embed_async(self, document_id: int, content: str) -> None:
        from src.knowledge.chunker import chunk_markdown
        from src.knowledge.embedder import OllamaEmbedder
        from src.knowledge.vector_store import VectorStore

        try:
            chunks = chunk_markdown(content)
            if not chunks:
                return
            embedder = OllamaEmbedder()
            try:
                embeddings = await embedder.embed_batch(chunks)
                if not any(e is not None for e in embeddings):
                    logger.info("wiki_embed_all_none", document_id=document_id)
                    return
                store = VectorStore(self._pool)
                await store.upsert_chunks(document_id, chunks, embeddings)
            finally:
                await embedder.close()
        except Exception as exc:
            logger.info(
                "wiki_embed_background_failed",
                document_id=document_id, error=str(exc)[:200],
            )

    async def _touch(self, path: str, word_count: int, content: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE wiki_documents
                SET word_count = $1, updated_at = NOW(),
                    content_tsv = to_tsvector('english', $2)
                WHERE path = $3
                """,
                word_count, content, path,
            )



_SYMBOL_ENRICH_SYSTEM_MSG = (
    "You are a senior equity research analyst maintaining an internal playbook "
    "for each ticker. The existing entry is a seed stub. Rewrite it using only "
    "the provided analysis history, fundamentals, and insider activity — do not "
    "invent facts, do not cite external sources.\n"
    "Rules:\n"
    "- 250 to 400 words\n"
    "- Structure: ## Overview / ## Valuation / ## Technical Setup / ## Insider & Sentiment / ## Playbook\n"
    "- End with 2-3 concrete trading heuristics derived from the data\n"
    "- Plain markdown, no preamble, no emoji"
)


def _build_symbol_enrichment_prompt(
    symbol: str,
    analysis_history: list[dict],
    fundamentals: dict | None,
    insider_trades: list[dict] | None,
) -> str:
    import json as _json

    parts: list[str] = [
        f"Rewrite the wiki entry for {symbol}.",
        "Data window: last 30 days of analysis scores, current fundamentals, last 90 days of insider trades.",
    ]

    if analysis_history:
        parts.append("\n## Analysis History")
        for row in analysis_history[:10]:
            fund = row.get("fundamental_score")
            tech = row.get("technical_score")
            comp = row.get("composite_score")
            regime = row.get("regime")
            parts.append(
                f"  - {row.get('date')}: composite={comp} fundamental={fund} "
                f"technical={tech} regime={regime}"
            )

    if fundamentals:
        parts.append("\n## Fundamentals (latest)")
        for key in (
            "pe_ratio", "pe_forward", "ps_ratio", "peg_ratio",
            "revenue_growth_1y", "revenue_growth_3y", "fcf_margin",
            "debt_to_equity", "sector", "sector_pe_avg",
        ):
            val = fundamentals.get(key)
            if val is not None:
                parts.append(f"  {key}: {val}")

    if insider_trades:
        buys = sum(1 for t in insider_trades if t.get("transaction_type") == "buy")
        sells = sum(1 for t in insider_trades if t.get("transaction_type") == "sell")
        parts.append("\n## Insider Activity (last 90d)")
        parts.append(f"  buys: {buys}, sells: {sells}")
        def _val(t: dict) -> float:
            try:
                return float(t.get("total_value") or 0)
            except (TypeError, ValueError):
                return 0.0
        top = sorted(insider_trades, key=_val, reverse=True)[:5]
        for t in top:
            parts.append(
                "  - "
                + _json.dumps({
                    "date": str(t.get("filing_date")),
                    "insider": t.get("insider_name"),
                    "type": t.get("transaction_type"),
                    "shares": float(t.get("shares") or 0),
                    "value": _val(t),
                }, default=str)
            )

    parts.append("\n## Task")
    parts.append(f"Produce the rewritten {symbol} playbook following the section structure in the rules.")
    return "\n".join(parts)


async def _generate_symbol_enrichment(prompt: str, symbol: str) -> tuple[str | None, str | None]:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.info("wiki_enrich_no_groq_key", symbol=symbol)
        return None, None

    from src.analysis.ai_summary import (
        CEREBRAS_FAST_MODEL,
        CEREBRAS_MODEL,
        DEFAULT_MODEL,
        FALLBACK_MODEL,
        _call_cerebras,
        _call_llm,
        _RateLimited,
    )
    from src.data.llm_client import build_fallback_chain

    attempts: list[tuple[str, str]] = build_fallback_chain(
        groq_fast=DEFAULT_MODEL, cerebras_fast=CEREBRAS_FAST_MODEL,
        groq_slow=FALLBACK_MODEL, cerebras_slow=CEREBRAS_MODEL,
    )
    for provider, model in attempts:
        try:
            if provider == "groq":
                result = await _call_llm(
                    api_key, model, prompt, symbol,
                    system_message=_SYMBOL_ENRICH_SYSTEM_MSG,
                )
            else:
                result = await _call_cerebras(
                    prompt, symbol, _SYMBOL_ENRICH_SYSTEM_MSG, model=model,
                )
            if result and result.summary:
                return result.summary, model
        except _RateLimited:
            continue
        except Exception as exc:
            logger.info(
                "wiki_enrich_attempt_failed",
                symbol=symbol, model=model, error=str(exc)[:120],
            )
            continue
    return None, None


def _compose_symbol_markdown(
    symbol: str, body: str, model_used: str | None,
) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = [
        f"# {symbol} Playbook",
        "",
        f"- **Generated (UTC):** {timestamp}",
        f"- **Source:** {model_used or 'seed stub'}",
        "",
    ]
    return "\n".join(header) + body.strip() + "\n"


async def enrich_symbol_doc(
    pool,
    symbol: str,
    analysis_history: list[dict],
    fundamentals: dict | None,
    insider_trades: list[dict] | None,
) -> tuple[int | None, str | None]:
    if not symbol:
        return None, None

    prompt = _build_symbol_enrichment_prompt(
        symbol, analysis_history or [], fundamentals, insider_trades,
    )
    body, model_used = await _generate_symbol_enrichment(prompt, symbol)
    if not body:
        logger.info("wiki_enrich_llm_unavailable_skipping", symbol=symbol)
        return None, None

    content = _compose_symbol_markdown(symbol, body, model_used)
    slug = symbol.lower().replace("/", "_")

    writer = WikiWriter(pool=pool)
    try:
        wiki_path = await writer.write_document(
            category="symbols",
            filename=slug,
            content=content,
            title=f"{symbol} Playbook",
            symbols=[symbol],
            confidence="established",
        )
    except Exception as exc:
        logger.error("wiki_enrich_write_failed", symbol=symbol, error=str(exc)[:200])
        return None, None

    doc_id: int | None = None
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id FROM wiki_documents WHERE path = $1", wiki_path,
                )
                if row:
                    doc_id = int(row["id"])
        except Exception as exc:
            logger.info("wiki_enrich_id_lookup_failed", symbol=symbol, error=str(exc)[:120])

    logger.info(
        "wiki_enriched",
        symbol=symbol, path=wiki_path, words=_count_words(content), model=model_used,
    )
    return doc_id, content

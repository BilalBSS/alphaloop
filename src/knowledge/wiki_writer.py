# / file i/o + db metadata for trading-wiki markdown docs
# / async locks per path to serialize concurrent writes
# / categories: regimes post-mortems strategies evolution symbols meta archive

from __future__ import annotations

import asyncio
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
# / strong refs to fire-and-forget embed tasks so the event loop doesn't gc them
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def get_wiki_root() -> Path:
    # / wiki root defaults to trading-wiki/ under project root
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
    # / lowercase, spaces→underscores, drop non [a-z0-9_-]
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_-]+", "_", s).strip("_")
    return s[:80] or "untitled"


def _validate_category(category: str) -> None:
    if category not in VALID_CATEGORIES:
        raise ValueError(f"invalid wiki category: {category!r}, must be one of {sorted(VALID_CATEGORIES)}")


def _count_words(text: str) -> int:
    return len(re.findall(r"\S+", text))


class WikiWriter:
    # / writes markdown files to trading-wiki/ + registers metadata in wiki_documents

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
        # / write (or overwrite) a markdown file, register in wiki_documents
        _validate_category(category)
        # / security: slugify filename to block ../, backslash, null, and other path-escape chars
        stem = filename[:-3] if filename.endswith(".md") else filename
        safe_stem = _slugify(stem)
        rel_path = f"{category}/{safe_stem}.md"
        abs_path = self._abs_path(rel_path)
        # / security: confirm resolved path stays under the wiki root
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

        # / fire-and-forget embedding; failures must never break the write
        if document_id is not None:
            try:
                task = asyncio.create_task(self._embed_async(document_id, content))
                _BACKGROUND_TASKS.add(task)
                task.add_done_callback(_BACKGROUND_TASKS.discard)
            except RuntimeError:
                # / no running loop (e.g., sync test context) — skip background embed
                pass
        return rel_path

    async def append_section(
        self,
        rel_path: str,
        heading: str,
        body: str,
    ) -> None:
        # / append a new section to an existing doc (atomic read-append-write)
        abs_path = self._abs_path(rel_path)
        lock = await _lock_for(rel_path)
        async with lock:
            if not abs_path.exists():
                raise FileNotFoundError(f"cannot append to missing doc: {rel_path}")
            async with aiofiles.open(abs_path, "r", encoding="utf-8") as f:
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
        async with aiofiles.open(abs_path, "r", encoding="utf-8") as f:
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
        # / move docs older than N days to archive/ — returns count archived
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
        # / background task: chunk + embed + upsert into wiki_embeddings
        # / any failure is logged and swallowed — never propagates to the caller
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
                # / prevent httpx client leak on fire-and-forget tasks
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

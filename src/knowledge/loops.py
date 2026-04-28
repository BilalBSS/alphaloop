# / orchestrator loops for knowledge base upkeep — registered by agent orchestrator

from __future__ import annotations


import structlog

from src.knowledge.chunker import chunk_markdown
from src.knowledge.embedder import OllamaEmbedder
from src.knowledge.vector_store import VectorStore
from src.knowledge.wiki_writer import WikiWriter

logger = structlog.get_logger(__name__)

# / defaults tuned for low background pressure
EMBED_LOOP_INTERVAL = 6 * 60 * 60   # / 6h
ARCHIVE_LOOP_INTERVAL = 24 * 60 * 60  # / 24h
ARCHIVE_OLDER_THAN_DAYS = 180
EMBED_BACKFILL_BATCH = 20


async def _embed_one_document(
    pool,
    embedder: OllamaEmbedder,
    store: VectorStore,
    writer: WikiWriter,
    doc: dict,
) -> bool:
    # / embed a single wiki document's chunks; returns True on success
    doc_id = doc["id"]
    rel_path = doc["path"]
    content = await writer.read_document(rel_path)
    if content is None:
        logger.info("wiki_embed_missing_file", path=rel_path)
        return False
    chunks = chunk_markdown(content)
    if not chunks:
        return False
    embeddings = await embedder.embed_batch(chunks)
    written = await store.upsert_chunks(doc_id, chunks, embeddings)
    return written > 0


async def _embed_backfill_once(pool) -> int:
    # / single pass over documents missing embeddings; closes embedder client before return
    embedder = OllamaEmbedder()
    store = VectorStore(pool)
    writer = WikiWriter(pool=pool)

    try:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT d.id, d.path, d.category
                    FROM wiki_documents d
                    LEFT JOIN wiki_embeddings e ON e.document_id = d.id
                    WHERE e.id IS NULL
                    ORDER BY d.updated_at DESC
                    LIMIT $1
                    """,
                    EMBED_BACKFILL_BATCH,
                )
        except Exception as exc:
            logger.warning("wiki_embed_fetch_failed", error=str(exc)[:120])
            return 0

        if not rows:
            return 0

        done = 0
        for row in rows:
            try:
                ok = await _embed_one_document(pool, embedder, store, writer, dict(row))
                if ok:
                    done += 1
            except Exception as exc:
                logger.info(
                    "wiki_embed_doc_failed",
                    path=row.get("path"), error=str(exc)[:120],
                )
        if done:
            logger.info("wiki_embed_backfill_progress", embedded=done, scanned=len(rows))
        return done
    finally:
        # / prevent httpx client leak across loop iterations
        await embedder.close()


async def wiki_embedding_backfill_loop(pool) -> int:
    # / one-shot backfill pass. orchestrator handles cadence via its own scheduler;
    # / the prior `while True` + sleep pattern conflicted with asyncio.wait_for
    # / in the orchestrator wrapper — the outer timeout always fired (900s).
    # / returns the number of documents embedded in this pass.
    try:
        return await _embed_backfill_once(pool)
    except Exception as exc:
        logger.error("wiki_embedding_loop_error", error=str(exc)[:200])
        return 0


async def wiki_archive_loop(pool) -> int:
    # / one-shot archive pass. same fix as wiki_embedding_backfill_loop — the
    # / infinite-loop shape was being wrapped by asyncio.wait_for in the
    # / orchestrator and timed out every 15 minutes without doing work.
    writer = WikiWriter(pool=pool)
    try:
        moved = await writer.archive_old(older_than_days=ARCHIVE_OLDER_THAN_DAYS)
        if moved:
            logger.info("wiki_archive_loop_moved", count=moved)
        return moved
    except Exception as exc:
        logger.error("wiki_archive_loop_error", error=str(exc)[:200])
        return 0

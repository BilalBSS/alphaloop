#!/usr/bin/env python3
# / idempotent embedder: finds wiki_documents missing wiki_embeddings rows, embeds them
# / safe to run multiple times; failures per doc are isolated
# / usage: python -m scripts.embed_wiki_backfill

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# / add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import structlog

from src.data.db import close_db, init_db
from src.knowledge.chunker import chunk_markdown
from src.knowledge.embedder import OllamaEmbedder
from src.knowledge.vector_store import VectorStore
from src.knowledge.wiki_writer import WikiWriter

logger = structlog.get_logger(__name__)


async def _fetch_missing(pool, limit: int | None) -> list[dict]:
    sql = """
        SELECT d.id, d.path, d.category, d.updated_at
        FROM wiki_documents d
        LEFT JOIN wiki_embeddings e ON e.document_id = d.id
        WHERE e.id IS NULL
        ORDER BY d.updated_at DESC
    """
    params: list = []
    if limit is not None:
        sql += " LIMIT $1"
        params.append(int(limit))
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [dict(r) for r in rows]


async def embed_all(limit: int | None = None) -> dict[str, int]:
    pool = await init_db()
    writer = WikiWriter(pool=pool)
    embedder = OllamaEmbedder()
    store = VectorStore(pool)

    summary = {"scanned": 0, "embedded": 0, "skipped": 0, "errors": 0}

    try:
        missing = await _fetch_missing(pool, limit)
        summary["scanned"] = len(missing)
        if not missing:
            logger.info("embed_backfill_no_work")
            return summary

        logger.info("embed_backfill_start", count=len(missing))

        for row in missing:
            doc_id = row["id"]
            rel_path = row["path"]
            try:
                content = await writer.read_document(rel_path)
                if content is None:
                    logger.info("embed_backfill_file_missing", path=rel_path)
                    summary["skipped"] += 1
                    continue
                chunks = chunk_markdown(content)
                if not chunks:
                    summary["skipped"] += 1
                    continue
                embeddings = await embedder.embed_batch(chunks)
                if not any(e is not None for e in embeddings):
                    logger.info("embed_backfill_all_none", path=rel_path)
                    summary["skipped"] += 1
                    continue
                written = await store.upsert_chunks(doc_id, chunks, embeddings)
                if written > 0:
                    summary["embedded"] += 1
                else:
                    summary["skipped"] += 1
            except Exception as exc:
                logger.warning(
                    "embed_backfill_doc_failed",
                    path=rel_path, error=str(exc)[:200],
                )
                summary["errors"] += 1
    finally:
        await embedder.close()
        await close_db()

    logger.info("embed_backfill_complete", **summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="backfill missing wiki embeddings")
    parser.add_argument("--limit", type=int, default=None,
                        help="max documents to process (default: all)")
    args = parser.parse_args()
    summary = asyncio.run(embed_all(limit=args.limit))
    print("embed backfill summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()

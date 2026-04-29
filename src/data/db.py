
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

_pool: asyncpg.Pool | None = None
_init_lock: asyncio.Lock | None = None
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _get_lock() -> asyncio.Lock:
    global _init_lock
    if _init_lock is None:
        _init_lock = asyncio.Lock()
    return _init_lock


async def init_db(database_url: str | None = None) -> asyncpg.Pool:
    global _pool

    if _pool is not None:
        return _pool

    async with _get_lock():
        if _pool is not None:
            return _pool

        url = database_url or os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError(
                "DATABASE_URL not set. use a VPS Postgres pooled connection string."
            )

        logger.info("connecting_to_database", url=_mask_url(url))

        async def _init_conn(conn):
            await conn.set_type_codec(
                'jsonb', encoder=lambda v: json.dumps(v, default=str),
                decoder=json.loads, schema='pg_catalog',
            )

        _pool = await asyncpg.create_pool(
            url,
            min_size=2,
            max_size=10,
            command_timeout=30,
            init=_init_conn,
        )

        await _run_migrations(_pool)
        logger.info("database_ready")
        return _pool


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database not initialized. call init_db() first.")
    return _pool


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("database_closed")


async def _run_migrations(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                id SERIAL PRIMARY KEY,
                filename VARCHAR(255) NOT NULL UNIQUE,
                applied_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        applied = {
            row["filename"]
            for row in await conn.fetch("SELECT filename FROM _migrations")
        }

        if not MIGRATIONS_DIR.exists():
            logger.info("no_migrations_dir", path=str(MIGRATIONS_DIR))
            return

        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

        for mf in migration_files:
            if mf.name in applied:
                continue

            sql = mf.read_text(encoding="utf-8")
            logger.info("applying_migration", filename=mf.name)

            try:
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO _migrations (filename) VALUES ($1)",
                        mf.name,
                    )
                logger.info("migration_applied", filename=mf.name)
            except Exception:
                logger.error("migration_failed", filename=mf.name, exc_info=True)
                raise


async def cleanup_old_data(pool: asyncpg.Pool) -> dict[str, int]:
    # / news_sentiment: 180d, crypto_onchain: 180d
    retention = {
        "news_sentiment": 180,
        "crypto_onchain": 180,
    }
    results = {}
    async with pool.acquire() as conn:
        for table, days in retention.items():
            try:
                date_col = "created_at" if table == "crypto_onchain" else "date"
                result = await conn.execute(
                    f"DELETE FROM {table} WHERE {date_col} < NOW() - INTERVAL '{days} days'"
                )
                count = int(result.split()[-1]) if result else 0
                results[table] = count
                if count > 0:
                    logger.info("cleanup_deleted", table=table, rows=count, retention_days=days)
            except Exception as exc:
                logger.warning("cleanup_failed", table=table, error=str(exc))
                results[table] = 0
    return results


def _mask_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        if parsed.password:
            masked = parsed._replace(
                netloc=f"{parsed.username}:***@{parsed.hostname}"
                + (f":{parsed.port}" if parsed.port else "")
            )
            return urlunparse(masked)
    except (ValueError, AttributeError):
        pass
    return url

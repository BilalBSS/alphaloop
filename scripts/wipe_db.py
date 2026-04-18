#!/usr/bin/env python3
# / wipe postgres data: TRUNCATE every table in public schema (except _migrations) CASCADE
# / preserves schema + migration history so no re-migration needed on restart
# / works for any DB user that owns the tables — doesn't need schema-owner privileges
# / usage: python -m scripts.wipe_db --yes

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


async def _list_tables(conn: asyncpg.Connection) -> list[tuple[str, int]]:
    # / return (table_name, row_count) for every user table in public schema
    rows = await conn.fetch("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    out = []
    for r in rows:
        name = r["table_name"]
        count_row = await conn.fetchrow(f'SELECT COUNT(*) AS c FROM "{name}"')
        out.append((name, int(count_row["c"])))
    return out


async def wipe(dry_run: bool, keep_migrations: bool) -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL not set")
        sys.exit(1)

    logger.info("wipe_db_start", dry_run=dry_run, keep_migrations=keep_migrations)

    conn = await asyncpg.connect(url)
    try:
        tables = await _list_tables(conn)
        total_rows = sum(c for _, c in tables)

        print(f"current state: {len(tables)} tables, {total_rows:,} total rows")
        for name, count in tables:
            if count > 0:
                marker = "  (kept)" if keep_migrations and name == "_migrations" else ""
                print(f"  {name}: {count:,} rows{marker}")

        targets = [t for t, _ in tables]
        if keep_migrations:
            targets = [t for t in targets if t != "_migrations"]

        if not targets:
            print("\nno tables to wipe (db is already empty or only _migrations exists).")
            return

        if dry_run:
            print(f"\ndry-run: would TRUNCATE {len(targets)} tables with CASCADE + RESTART IDENTITY.")
            print("rerun with --yes to execute.")
            return

        quoted = ", ".join(f'"{t}"' for t in targets)
        stmt = f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"
        print(f"\nexecuting: TRUNCATE {len(targets)} tables CASCADE...")
        await conn.execute(stmt)
        logger.info("wipe_complete", tables_truncated=len(targets))
        print(f"  truncated {len(targets)} tables. sequences reset. _migrations {'preserved' if keep_migrations else 'wiped'}.")
    finally:
        await conn.close()

    print("\ndb is wiped. tables still exist, all rows gone.")
    print("next steps:")
    print("  1. python -m scripts.backfill --years 5    # market_data, fundamentals, insider")
    print("  2. python -m scripts.seed_wiki              # wiki_documents")
    print("  3. python -m scripts.embed_wiki_backfill    # wiki_embeddings")
    print("  4. sudo systemctl start quant-trading quant-dashboard")


def main() -> None:
    parser = argparse.ArgumentParser(description="wipe postgres data: TRUNCATE all tables CASCADE")
    parser.add_argument("--yes", action="store_true", help="actually execute the wipe (default is dry-run)")
    parser.add_argument("--wipe-migrations", action="store_true", help="also truncate _migrations (forces migrations to re-run)")
    args = parser.parse_args()

    asyncio.run(wipe(dry_run=not args.yes, keep_migrations=not args.wipe_migrations))


if __name__ == "__main__":
    main()

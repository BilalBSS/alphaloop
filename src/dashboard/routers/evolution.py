from __future__ import annotations

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.dashboard.helpers import db, serializers
from src.dashboard.state import STATE

logger = structlog.get_logger(__name__)

router = APIRouter()

_VALID_WIKI_CATEGORIES = {
    "regimes", "post-mortems", "strategies", "evolution", "symbols", "meta", "archive",
}


@router.get("/api/evolution")
async def get_evolution():
    rows = await db.query(
        """SELECT * FROM evolution_log
        ORDER BY generation DESC, created_at DESC LIMIT 50"""
    )
    return serializers.serialize(rows)


@router.get("/api/evolution/mutations")
async def get_evolution_mutations(limit: int = 100):
    # / wiki-guided A/B feed
    limit = max(1, min(int(limit), 500))
    if STATE.pool is None:
        return {"mutations": [], "wiki_guided_count": 0, "random_count": 0, "wiki_win_rate": None, "random_win_rate": None}
    rows = await db.query(
        """SELECT id, generation, parent_strategy_id, mutant_strategy_id,
                wiki_guided, wiki_context_tokens, parent_sharpe, mutant_sharpe,
                sharpe_delta, survived, created_at
        FROM evolution_mutations
        ORDER BY created_at DESC LIMIT $1""",
        limit,
    )
    mutations = serializers.serialize(rows)
    wiki_rows = [m for m in mutations if m.get("wiki_guided")]
    rand_rows = [m for m in mutations if not m.get("wiki_guided")]
    wiki_survived = [m for m in wiki_rows if m.get("survived") is True]
    rand_survived = [m for m in rand_rows if m.get("survived") is True]
    wiki_win = (len(wiki_survived) / len(wiki_rows)) if wiki_rows else None
    rand_win = (len(rand_survived) / len(rand_rows)) if rand_rows else None
    return {
        "mutations": mutations,
        "wiki_guided_count": len(wiki_rows),
        "random_count": len(rand_rows),
        "wiki_win_rate": round(wiki_win, 3) if wiki_win is not None else None,
        "random_win_rate": round(rand_win, 3) if rand_win is not None else None,
    }


@router.get("/api/wiki/documents")
async def get_wiki_documents(
    category: str | None = None,
    symbol: str | None = None,
    strategy_id: str | None = None,
    limit: int = 200,
):
    limit = max(1, min(int(limit), 500))
    clauses: list[str] = []
    params: list = []
    if category:
        if category not in _VALID_WIKI_CATEGORIES:
            return JSONResponse({"error": "invalid category"}, status_code=400)
        params.append(category)
        clauses.append(f"category = ${len(params)}")
    if symbol:
        params.append(symbol.upper())
        clauses.append(f"${len(params)} = ANY(symbols)")
    if strategy_id:
        params.append(strategy_id)
        clauses.append(f"${len(params)} = ANY(strategy_ids)")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    sql = (
        f"SELECT id, path, category, title, symbols, strategy_ids, "
        f"word_count, confidence, created_at, updated_at "
        f"FROM wiki_documents {where} "
        f"ORDER BY updated_at DESC LIMIT ${len(params)}"
    )
    rows = await db.query(sql, *params)
    return serializers.serialize(rows)


@router.get("/api/wiki/document")
async def get_wiki_document(path: str):
    if not path or ".." in path or path.startswith("/") or "\\" in path or "\x00" in path:
        return JSONResponse({"error": "invalid path"}, status_code=400)
    row = await db.query_one(
        "SELECT path, category, title FROM wiki_documents WHERE path = $1", path,
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        from src.knowledge.wiki_writer import WikiWriter, get_wiki_root
        root = get_wiki_root().resolve()
        candidate = (root / path).resolve()
        if not str(candidate).startswith(str(root)):
            logger.warning("wiki_read_path_escape_blocked", path=path)
            return JSONResponse({"error": "invalid path"}, status_code=400)
        writer = WikiWriter(pool=STATE.pool)
        content = await writer.read_document(path)
    except Exception as exc:
        logger.warning("wiki_read_failed", path=path, error=str(exc)[:120])
        return JSONResponse({"error": "read failed"}, status_code=500)
    if content is None:
        return JSONResponse({"error": "file missing"}, status_code=404)
    return {
        "path": row["path"],
        "category": row["category"],
        "title": row["title"],
        "content": content,
    }


@router.get("/api/post-mortems")
async def get_post_mortems(strategy_id: str | None = None, limit: int = 50):
    limit = max(1, min(int(limit), 200))
    if strategy_id:
        sql = (
            "SELECT id, strategy_id, symbol, trigger_type, pnl, expected_pnl, "
            "deviation_sigma, details, wiki_path, created_at FROM post_mortems "
            "WHERE strategy_id = $1 ORDER BY created_at DESC LIMIT $2"
        )
        rows = await db.query(sql, strategy_id, limit)
    else:
        sql = (
            "SELECT id, strategy_id, symbol, trigger_type, pnl, expected_pnl, "
            "deviation_sigma, details, wiki_path, created_at FROM post_mortems "
            "ORDER BY created_at DESC LIMIT $1"
        )
        rows = await db.query(sql, limit)
    return serializers.serialize(rows)


@router.get("/api/observation-log")
async def get_observation_log(hours: int = 24, limit: int = 20):
    # / near-miss tracker (migration 052)
    hours = max(1, min(hours, 168))
    limit = max(1, min(limit, 100))
    by_strategy = await db.query(
        """SELECT strategy_id,
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE near_miss_type = 'n_minus_1_technical') AS n_minus_1,
            COUNT(*) FILTER (WHERE near_miss_type = 'fundamental_gate') AS fundamental_gate,
            MAX(created_at) AS last_seen
        FROM observation_log
        WHERE created_at >= NOW() - ($1 || ' hours')::interval
        GROUP BY strategy_id
        ORDER BY COUNT(*) DESC""",
        str(hours),
    )
    recent = await db.query(
        """SELECT strategy_id, symbol, near_miss_type, passed_count, total_count,
            failed_reason, created_at
        FROM observation_log
        WHERE created_at >= NOW() - ($1 || ' hours')::interval
        ORDER BY created_at DESC
        LIMIT $2""",
        str(hours), limit,
    )
    return {
        "hours": hours,
        "by_strategy": serializers.serialize(by_strategy),
        "recent": serializers.serialize(recent),
    }

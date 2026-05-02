from __future__ import annotations

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.dashboard.helpers import db, serializers

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/api/wiki/retrieval/latest")
async def get_latest_retrieval():
    row = await db.query_one(
        """SELECT * FROM retrieval_logs
        ORDER BY ts DESC LIMIT 1""",
    )
    if not row:
        return {"cycle_id": None, "retrieved": [], "prompt_tokens": 0}
    return serializers.serialize_one(row)


@router.get("/api/wiki/retrieval/{cycle_id}")
async def get_retrieval(cycle_id: str):
    row = await db.query_one(
        "SELECT * FROM retrieval_logs WHERE cycle_id = $1", cycle_id,
    )
    if not row:
        return JSONResponse({"error": "cycle not found"}, status_code=404)
    return serializers.serialize_one(row)

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.dashboard.helpers import serializers
from src.dashboard.helpers.auth import check_admin_token, extract_bearer
from src.dashboard.state import STATE

router = APIRouter()


@router.get("/api/loops")
async def get_loops():
    from src.data.loop_registry import describe_loops
    rows = await describe_loops(STATE.pool)
    return {"loops": serializers.serialize(rows)}


@router.post("/api/admin/trigger/{service}")
async def admin_trigger(service: str, request: Request):
    from src.data.loop_registry import LOOP_METADATA, enqueue_trigger
    if not STATE.admin_token:
        return JSONResponse(
            {"error": "admin_token_not_configured",
             "hint": "set ADMIN_TOKEN in .env (>=32 chars) to enable this endpoint"},
            status_code=503,
        )
    supplied = extract_bearer(request)
    if not check_admin_token(supplied):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if service not in LOOP_METADATA:
        return JSONResponse({"error": "unknown_service", "service": service}, status_code=404)
    row_id = await enqueue_trigger(STATE.pool, service)
    if row_id is None:
        return JSONResponse({"error": "enqueue_failed"}, status_code=500)
    return JSONResponse({"trigger_id": row_id, "service": service, "status": "queued"}, status_code=202)


@router.get("/api/admin/pause")
async def admin_pause_status():
    from src.agents.system_flags import is_executor_paused
    paused = await is_executor_paused(STATE.pool)
    return {"paused": bool(paused)}


@router.post("/api/admin/pause")
async def admin_pause_set(request: Request):
    from src.agents.system_flags import set_executor_paused
    if not STATE.admin_token:
        return JSONResponse(
            {"error": "admin_token_not_configured",
             "hint": "set ADMIN_TOKEN in .env (>=32 chars) to enable this endpoint"},
            status_code=503,
        )
    supplied = extract_bearer(request)
    if not check_admin_token(supplied):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = {}
    try:
        body = await request.json()
    except (ValueError, TypeError):
        body = {}
    paused = bool(body.get("paused")) if isinstance(body, dict) else False
    await set_executor_paused(STATE.pool, paused)
    return {"paused": paused}


@router.get("/api/env-health")
async def get_env_health():
    required = [
        "DATABASE_URL", "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
        "GROQ_API_KEY", "DEEPSEEK_API_KEY", "CEREBRAS_API_KEY",
        "FRED_API_KEY", "FINNHUB_API_KEY", "SEC_EDGAR_USER_AGENT",
    ]
    optional = [
        "OLLAMA_BASE_URL", "TRADE_SYMBOLS",
        "DUNE_API_KEY", "DISCORD_WEBHOOK_URL", "SLACK_WEBHOOK_URL",
        "TELEGRAM_BOT_TOKEN", "ADMIN_TOKEN", "KRONOS_ENABLED",
        "WIKI_HYDRATION_DAILY_CAP", "MAX_POSITION_PCT", "CONSENSUS_MODE",
        "ML_FEATURE_SET",
    ]
    return {
        "required": {k: bool(os.environ.get(k)) for k in required},
        "optional": {k: bool(os.environ.get(k)) for k in optional},
    }

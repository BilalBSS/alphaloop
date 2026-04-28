from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from src.dashboard import drawings as drawings_mod
from src.dashboard.helpers.auth import require_admin_token
from src.dashboard.routers._common import CHART_STATE_SYMBOL_MAX
from src.dashboard.state import STATE

router = APIRouter()


@router.get("/api/drawings/{symbol}")
async def list_drawings_endpoint(symbol: str):
    if not symbol or len(symbol) > CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return []
    return await drawings_mod.list_drawings(STATE.pool, symbol)


@router.post("/api/drawings/{symbol}")
async def create_drawing_endpoint(symbol: str, body: dict, _auth: None = Depends(require_admin_token)):
    if not symbol or len(symbol) > CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return JSONResponse(status_code=503, content={"error": "db_not_ready"})
    raw_type = body.get("drawing_type") or body.get("type") or ""
    dt = drawings_mod.sanitize_drawing_type(raw_type)
    if dt is None:
        return JSONResponse(status_code=400, content={"error": "invalid_drawing_type"})
    payload = body.get("payload")
    if not drawings_mod.validate_payload(payload):
        return JSONResponse(status_code=400, content={"error": "invalid_payload"})
    return await drawings_mod.create_drawing(STATE.pool, symbol, dt, payload)


@router.put("/api/drawings/{symbol}/{drawing_id}")
async def update_drawing_endpoint(symbol: str, drawing_id: int, body: dict, _auth: None = Depends(require_admin_token)):
    if not symbol or len(symbol) > CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return JSONResponse(status_code=503, content={"error": "db_not_ready"})
    payload = body.get("payload")
    if not drawings_mod.validate_payload(payload):
        return JSONResponse(status_code=400, content={"error": "invalid_payload"})
    result = await drawings_mod.update_drawing(STATE.pool, symbol, drawing_id, payload)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return result


@router.delete("/api/drawings/{symbol}/{drawing_id}")
async def delete_drawing_endpoint(symbol: str, drawing_id: int, _auth: None = Depends(require_admin_token)):
    if not symbol or len(symbol) > CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return JSONResponse(status_code=503, content={"error": "db_not_ready"})
    ok = await drawings_mod.delete_drawing(STATE.pool, symbol, drawing_id)
    return {"deleted": bool(ok)}

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from src.dashboard import alerts as alerts_mod
from src.dashboard.helpers.auth import require_admin_token
from src.dashboard.routers._common import CHART_STATE_SYMBOL_MAX
from src.dashboard.state import STATE

router = APIRouter()


@router.get("/api/alerts")
async def list_all_alerts_endpoint():
    if STATE.pool is None:
        return []
    return await alerts_mod.list_alerts(STATE.pool, status=alerts_mod.STATUS_ACTIVE)


@router.get("/api/alerts/{symbol}")
async def list_alerts_endpoint(symbol: str):
    if not symbol or len(symbol) > CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return []
    return await alerts_mod.list_alerts(STATE.pool, symbol=symbol, status=alerts_mod.STATUS_ACTIVE)


@router.post("/api/alerts/{symbol}")
async def create_alert_endpoint(symbol: str, body: dict, _auth: None = Depends(require_admin_token)):
    if not symbol or len(symbol) > CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return JSONResponse(status_code=503, content={"error": "db_not_ready"})
    result = await alerts_mod.create_alert(
        STATE.pool,
        symbol,
        body.get("price"),
        body.get("direction"),
        body.get("label"),
    )
    if isinstance(result, dict) and result.get("error"):
        return JSONResponse(status_code=400, content=result)
    return result


@router.put("/api/alerts/{symbol}/{alert_id}")
async def update_alert_endpoint(symbol: str, alert_id: int, body: dict, _auth: None = Depends(require_admin_token)):
    if not symbol or len(symbol) > CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return JSONResponse(status_code=503, content={"error": "db_not_ready"})
    patch = {k: body[k] for k in ("price", "direction", "label", "status") if k in body}
    if not patch:
        return JSONResponse(status_code=400, content={"error": "empty_patch"})
    result = await alerts_mod.update_alert(STATE.pool, symbol, alert_id, **patch)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return result


@router.delete("/api/alerts/{symbol}/{alert_id}")
async def delete_alert_endpoint(symbol: str, alert_id: int, _auth: None = Depends(require_admin_token)):
    if not symbol or len(symbol) > CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return JSONResponse(status_code=503, content={"error": "db_not_ready"})
    ok = await alerts_mod.delete_alert(STATE.pool, symbol, alert_id)
    return {"deleted": bool(ok)}

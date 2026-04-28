from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.dashboard import marker_aggregator as marker_agg_mod
from src.dashboard.routers._common import CHART_STATE_SYMBOL_MAX
from src.dashboard.state import STATE

router = APIRouter()


@router.get("/api/markers/{symbol}")
async def get_markers_endpoint(
    symbol: str,
    kinds: str = "trades,signals,insiders,earnings,regime,consensus",
    days: int = 30,
):
    # / unified markers endpoint — returns a dict keyed by marker kind
    if not symbol or len(symbol) > CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return {"trades": [], "signals": [], "insiders": [], "earnings": [], "regime": [], "consensus": []}
    days = max(1, min(days, 365))
    requested = {k.strip() for k in kinds.split(",") if k.strip()}
    if not requested:
        return {}
    return await marker_agg_mod.build_markers(STATE.pool, symbol, requested, days)

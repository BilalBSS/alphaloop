from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.dashboard import compare as compare_mod
from src.dashboard.routers._common import CHART_STATE_SYMBOL_MAX
from src.dashboard.state import STATE

router = APIRouter()


@router.get("/api/compare")
async def compare_endpoint(
    base: str = "",
    against: str = "",
    symbols: str = "",
    timeframe: str = "1Day",
    days: int = 90,
):
    # / pair normalized overlay — % change from first common timestamp
    if not base and not against and symbols:
        parts = [s.strip() for s in symbols.split(",") if s.strip()]
        if len(parts) >= 2:
            base, against = parts[0], parts[1]
    if not base or not against:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if len(base) > CHART_STATE_SYMBOL_MAX or len(against) > CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return {
            "base": base,
            "against": against,
            "timeframe": timeframe,
            "days": days,
            "base_series": [],
            "against_series": [],
            "common_count": 0,
        }
    return await compare_mod.fetch_compare(STATE.pool, base, against, timeframe, days)

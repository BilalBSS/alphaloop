from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.dashboard import volume_profile as volume_profile_mod
from src.dashboard.routers._common import CHART_STATE_SYMBOL_MAX
from src.dashboard.state import STATE

router = APIRouter()


@router.get("/api/volume-profile/{symbol}")
async def volume_profile_endpoint(symbol: str, bins: int = 24, days: int = 30, timeframe: str = "1Hour"):
    # / horizontal histogram of traded volume at price levels + poc/vah/val anchors
    if not symbol or len(symbol) > CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return {
            "symbol": symbol,
            "bins": [],
            "poc": None,
            "vah": None,
            "val": None,
            "total_volume": 0.0,
            "bin_count": bins,
            "days": days,
            "timeframe": timeframe,
        }
    return await volume_profile_mod.fetch_volume_profile(STATE.pool, symbol, bins, days, timeframe)

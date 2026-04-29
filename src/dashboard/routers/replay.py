from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.dashboard import replay as replay_mod
from src.dashboard.routers._common import CHART_STATE_SYMBOL_MAX
from src.dashboard.state import STATE

router = APIRouter()


@router.get("/api/replay/{symbol}")
async def replay_endpoint(symbol: str, cutoff: str = "", days_back: int = 30):
    if not symbol or len(symbol) > CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return {
            "symbol": symbol,
            "cutoff": cutoff,
            "min_t": None,
            "max_t": None,
            "bars": {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []},
            "trades": [],
            "signals": [],
            "consensus": [],
        }
    return await replay_mod.fetch_replay_snapshot(STATE.pool, symbol, cutoff, days_back)

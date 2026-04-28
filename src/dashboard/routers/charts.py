from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from src.dashboard import chart_state as chart_state_mod
from src.dashboard import indicator_registry
from src.dashboard.helpers import db, serializers
from src.dashboard.helpers.auth import require_admin_token
from src.dashboard.routers._common import CHART_STATE_SYMBOL_MAX
from src.dashboard.state import STATE

router = APIRouter()


@router.get("/api/indicators/{symbol}")
async def get_indicators(symbol: str, limit: int = 60, timeframe: str = "1Day"):
    limit = max(1, min(limit, 250))
    rows = await db.query(
        """SELECT date, rsi14, macd, macd_signal, macd_histogram,
        adx, sma20, sma50, bb_upper, bb_middle, bb_lower, atr, hurst, timeframe
        FROM computed_indicators
        WHERE symbol = $1 AND timeframe = $2 ORDER BY date DESC LIMIT $3""",
        symbol, timeframe, limit,
    )
    return serializers.serialize(rows)


def _intraday_cache_key(symbol: str, timeframe: str, days: int, ids: tuple[str, ...]) -> tuple:
    return (symbol, timeframe, days, ids)


@router.get("/api/intraday/{symbol}")
async def get_intraday(symbol: str, days: int = 10, timeframe: str = "1Hour", indicators: str = ""):
    days = max(1, min(days, 60))

    ids_sorted: tuple[str, ...] = tuple(sorted(i.strip() for i in indicators.split(",") if i.strip()))
    cache_key = _intraday_cache_key(symbol, timeframe, days, ids_sorted)
    cached = STATE.intraday_cache.get(cache_key)
    if cached is not None:
        return cached

    pool_ready = STATE.pool is not None

    rows = await db.query(
        """SELECT timestamp, open, high, low, close, volume, vwap
        FROM market_data_intraday
        WHERE symbol = $1 AND timeframe = $2
            AND timestamp > NOW() - ($3 || ' days')::INTERVAL
        ORDER BY timestamp ASC""",
        symbol, timeframe, str(days),
    )
    if not ids_sorted:
        payload = serializers.serialize(rows)
        if pool_ready and rows:
            STATE.intraday_cache.put(cache_key, payload)
        return payload

    if not rows:
        payload = {
            "bars": {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []},
            "indicators": {},
            "meta": {"symbol": symbol, "timeframe": timeframe, "bar_count": 0},
        }
        if pool_ready:
            STATE.intraday_cache.put(cache_key, payload)
        return payload

    import pandas as pd

    t_list: list[str] = []
    o_list: list[float] = []
    h_list: list[float] = []
    l_list: list[float] = []
    c_list: list[float] = []
    v_list: list[float] = []
    for r in rows:
        ts = r.get("timestamp")
        t_list.append(ts.isoformat() if hasattr(ts, "isoformat") else str(ts))
        o_list.append(float(r["open"]) if r.get("open") is not None else float("nan"))
        h_list.append(float(r["high"]) if r.get("high") is not None else float("nan"))
        l_list.append(float(r["low"]) if r.get("low") is not None else float("nan"))
        c_list.append(float(r["close"]) if r.get("close") is not None else float("nan"))
        v_list.append(float(r["volume"]) if r.get("volume") is not None else 0.0)

    df = pd.DataFrame({
        "open": o_list,
        "high": h_list,
        "low": l_list,
        "close": c_list,
        "volume": v_list,
    })

    computed: dict = {}
    for ind_id in ids_sorted:
        result = indicator_registry.compute(df, ind_id)
        if result is not None:
            computed[ind_id] = result

    payload = {
        "bars": {
            "t": t_list,
            "o": o_list,
            "h": h_list,
            "l": l_list,
            "c": c_list,
            "v": v_list,
        },
        "indicators": computed,
        "meta": {"symbol": symbol, "timeframe": timeframe, "bar_count": len(rows)},
    }
    if pool_ready:
        STATE.intraday_cache.put(cache_key, payload)
    return payload


@router.get("/api/chart-state/{symbol}")
async def get_chart_state_endpoint(symbol: str):
    if not symbol or len(symbol) > CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return {"symbol": symbol, "timeframe": "1Hour", "active_indicators": [], "indicator_params": {}}
    return await chart_state_mod.get_chart_state(STATE.pool, symbol)


@router.post("/api/chart-state/{symbol}")
async def upsert_chart_state_endpoint(symbol: str, body: dict, _auth: None = Depends(require_admin_token)):
    if not symbol or len(symbol) > CHART_STATE_SYMBOL_MAX:
        return JSONResponse(status_code=400, content={"error": "invalid_symbol"})
    if STATE.pool is None:
        return {"error": "db_not_ready"}
    ids = body.get("active_indicators")
    if ids is not None:
        ids = chart_state_mod.sanitize_indicators(ids)
    params = body.get("indicator_params")
    if params is not None and not isinstance(params, dict):
        params = None
    return await chart_state_mod.upsert_chart_state(
        STATE.pool,
        symbol,
        timeframe=body.get("timeframe"),
        active_indicators=ids,
        indicator_params=params,
    )

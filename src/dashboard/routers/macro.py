from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.dashboard.helpers import db, serializers
from src.dashboard.state import STATE

router = APIRouter()


@router.get("/api/macro-context")
async def get_macro_context():
    if STATE.pool is None:
        return {"indicators": [], "yield_curve_spread": None}
    rows = await db.query(
        """SELECT DISTINCT ON (series_id) series_id, date, value, normalized
        FROM macro_data
        ORDER BY series_id, date DESC"""
    )
    by_series = {r["series_id"]: r for r in rows}
    spread = None
    dgs10 = by_series.get("DGS10")
    dgs2 = by_series.get("DGS2")
    if dgs10 and dgs2:
        try:
            raw = float(dgs10["value"]) - float(dgs2["value"])
            spread = {
                "value": round(raw, 3),
                "normalized": round(max(-1.0, min(1.0, raw / 2.0)), 3),
                "inverted": raw < 0,
            }
        except (TypeError, ValueError):
            spread = None
    cpi_yoy = await _cpi_yoy(by_series.get("CPIAUCSL"))
    return {
        "indicators": serializers.serialize(rows),
        "yield_curve_spread": spread,
        "cpi_yoy": cpi_yoy,
    }


async def _cpi_yoy(latest_cpi) -> float | None:
    # / cpi index to yoy
    if not latest_cpi:
        return None
    try:
        current = float(latest_cpi["value"])
    except (TypeError, ValueError):
        return None
    prior = await db.query_one(
        """SELECT value FROM macro_data
        WHERE series_id = 'CPIAUCSL' AND date <= ($1::date - INTERVAL '1 year')
        ORDER BY date DESC LIMIT 1""",
        latest_cpi["date"],
    )
    if not prior or prior.get("value") in (None, 0):
        return None
    try:
        base = float(prior["value"])
    except (TypeError, ValueError):
        return None
    if base <= 0:
        return None
    return round((current / base - 1.0) * 100.0, 2)


@router.get("/api/macro-history")
async def get_macro_history(days: int = 180):
    # / per-series timeseries for sparklines
    if STATE.pool is None:
        return {"series": {}}
    days = max(7, min(int(days or 180), 730))
    rows = await db.query(
        """SELECT series_id, date, value FROM macro_data
        WHERE date >= CURRENT_DATE - ($1::int * INTERVAL '1 day')
        ORDER BY series_id, date ASC""",
        days,
    )
    out: dict[str, list[dict]] = {}
    for r in rows:
        sid = r["series_id"]
        out.setdefault(sid, []).append({
            "date": r["date"].isoformat() if hasattr(r["date"], "isoformat") else r["date"],
            "value": float(r["value"]) if r["value"] is not None else None,
        })
    return {"series": out, "days": days}


@router.get("/api/regime-shifts")
async def get_regime_shifts(market: str | None = None, limit: int = 50):
    limit = max(1, min(int(limit), 200))
    if market:
        if market not in ("equity", "crypto"):
            return JSONResponse({"error": "invalid market"}, status_code=400)
        sql = (
            "SELECT id, old_regime, new_regime, market, confidence, wiki_path, detected_at "
            "FROM regime_shifts WHERE market = $1 ORDER BY detected_at DESC LIMIT $2"
        )
        rows = await db.query(sql, market, limit)
    else:
        sql = (
            "SELECT id, old_regime, new_regime, market, confidence, wiki_path, detected_at "
            "FROM regime_shifts ORDER BY detected_at DESC LIMIT $1"
        )
        rows = await db.query(sql, limit)
    return serializers.serialize(rows)


@router.get("/api/regime-timeline")
async def get_regime_timeline(market: str = "equity", days: int = 180):
    if market not in ("equity", "crypto"):
        return JSONResponse({"error": "invalid market"}, status_code=400)
    days = max(1, min(days, 3650))
    if STATE.pool is None:
        return {"market": market, "days": days, "history": [], "shifts": []}
    history = await db.query(
        """SELECT date, regime, confidence, volatility_20d, trend_sma50_above_200, drawdown_from_high
        FROM regime_history
        WHERE market = $1 AND date >= CURRENT_DATE - ($2 || ' days')::INTERVAL
        ORDER BY date ASC""",
        market, str(days),
    )
    shifts = await db.query(
        """SELECT id, old_regime, new_regime, confidence, wiki_path, detected_at
        FROM regime_shifts
        WHERE market = $1 AND detected_at >= NOW() - ($2 || ' days')::INTERVAL
        ORDER BY detected_at ASC""",
        market, str(days),
    )
    return {
        "market": market,
        "days": days,
        "history": serializers.serialize(history),
        "shifts": serializers.serialize(shifts),
    }

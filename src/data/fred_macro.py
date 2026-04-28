# / fred macro data: yield curve, cpi, fed funds, unemployment
# / normalizes each series to -1.0 to 1.0 score

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import structlog

from .resilience import api_get, configure_rate_limit, with_retry

logger = structlog.get_logger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

configure_rate_limit("fred", max_concurrent=3, delay=0.5)

SERIES_CONFIG: dict[str, dict[str, float]] = {
    "DGS10": {"neutral": 3.5, "range": 2.5},
    "DGS2": {"neutral": 3.5, "range": 2.5},
    "CPIAUCSL": {"neutral": 2.5, "range": 3.0},
    "FEDFUNDS": {"neutral": 3.0, "range": 3.0},
    "UNRATE": {"neutral": 4.5, "range": 3.0},
}


def _fred_params(series_id: str, days: int = 180) -> dict[str, str]:
    # / pull 180 days so monthly series (CPI, FEDFUNDS, UNRATE) always have at
    # / least one observation and daily series (DGS10/DGS2) have a full sparkline window
    key = os.environ.get("FRED_API_KEY", "")
    start = (date.today() - timedelta(days=days)).isoformat()
    return {
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "observation_start": start,
        "sort_order": "desc",
        "limit": "200",
    }


def _normalize(series_id: str, value: float) -> float:
    cfg = SERIES_CONFIG.get(series_id)
    if not cfg:
        return 0.0
    delta = value - cfg["neutral"]
    # / higher unemployment/cpi = bearish
    if series_id in ("UNRATE", "CPIAUCSL"):
        delta = -delta
    normalized = delta / cfg["range"]
    return max(-1.0, min(1.0, normalized))


@with_retry(source="fred", max_retries=2, base_delay=1.0)
async def _fetch_series(series_id: str) -> list[dict[str, Any]]:
    # / returns every valid observation in window, oldest first
    if not os.environ.get("FRED_API_KEY"):
        return []
    params = _fred_params(series_id)
    resp = await api_get(FRED_BASE, params=params, source="fred")
    data = resp.json()
    observations = data.get("observations", [])
    out: list[dict[str, Any]] = []
    for obs in observations:
        val_str = obs.get("value", ".")
        if val_str == ".":
            continue
        try:
            value = float(val_str)
            out.append({
                "series_id": series_id,
                "date": obs["date"],
                "value": value,
                "normalized": _normalize(series_id, value),
            })
        except (ValueError, TypeError):
            continue
    # / fred returns desc; flip to asc so insert order + sparkline draw left->right
    out.sort(key=lambda r: r["date"])
    return out


async def fetch_macro_indicators(pool: Any) -> dict[str, Any]:
    # / returns the latest point per series (plus yield-curve spread) for the
    # / analyst/strategy layer, but writes the full window to macro_data so the
    # / dashboard's /api/macro-history can render sparklines.
    results: dict[str, Any] = {}
    for series_id in SERIES_CONFIG:
        try:
            points = await _fetch_series(series_id)
            if not points:
                continue
            async with pool.acquire() as conn:
                await conn.executemany(
                    """
                    INSERT INTO macro_data (date, series_id, value, normalized)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (date, series_id) DO UPDATE SET
                        value = EXCLUDED.value, normalized = EXCLUDED.normalized
                    """,
                    [(date.fromisoformat(p["date"]), series_id, p["value"], p["normalized"])
                     for p in points],
                )
            latest = points[-1]
            results[series_id] = latest
        except Exception as exc:
            logger.warning("fred_fetch_failed", series=series_id, error=str(exc))

    dgs10 = results.get("DGS10", {}).get("value")
    dgs2 = results.get("DGS2", {}).get("value")
    if dgs10 is not None and dgs2 is not None:
        spread = dgs10 - dgs2
        spread_normalized = max(-1.0, min(1.0, spread / 2.0))
        results["yield_curve_spread"] = {
            "value": spread, "normalized": spread_normalized, "inverted": spread < 0,
        }
    logger.info("fred_macro_fetched", series_count=len(results))
    return results


def get_macro_score(indicators: dict[str, Any]) -> float:
    scores: list[float] = []
    for data in indicators.values():
        if isinstance(data, dict) and "normalized" in data:
            scores.append(data["normalized"])
    if not scores:
        return 0.0
    return max(-1.0, min(1.0, sum(scores) / len(scores)))

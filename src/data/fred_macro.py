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


def _fred_params(series_id: str, days: int = 90) -> dict[str, str]:
    key = os.environ.get("FRED_API_KEY", "")
    start = (date.today() - timedelta(days=days)).isoformat()
    return {
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "observation_start": start,
        "sort_order": "desc",
        "limit": "10",
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
async def _fetch_series(series_id: str) -> dict[str, Any] | None:
    if not os.environ.get("FRED_API_KEY"):
        return None
    params = _fred_params(series_id)
    resp = await api_get(FRED_BASE, params=params, source="fred")
    data = resp.json()
    observations = data.get("observations", [])
    if not observations:
        return None
    for obs in observations:
        val_str = obs.get("value", ".")
        if val_str == ".":
            continue
        try:
            value = float(val_str)
            return {
                "series_id": series_id,
                "date": obs["date"],
                "value": value,
                "normalized": _normalize(series_id, value),
            }
        except (ValueError, TypeError):
            continue
    return None


async def fetch_macro_indicators(pool: Any) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for series_id in SERIES_CONFIG:
        try:
            data = await _fetch_series(series_id)
            if data:
                results[series_id] = data
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO macro_data (date, series_id, value, normalized)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (date, series_id) DO UPDATE SET
                            value = EXCLUDED.value, normalized = EXCLUDED.normalized
                        """,
                        date.fromisoformat(data["date"]), series_id,
                        data["value"], data["normalized"],
                    )
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
    for key, data in indicators.items():
        if isinstance(data, dict) and "normalized" in data:
            scores.append(data["normalized"])
    if not scores:
        return 0.0
    return max(-1.0, min(1.0, sum(scores) / len(scores)))

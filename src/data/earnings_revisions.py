# / earnings revisions: finnhub eps estimates + revision momentum
# / tracks estimate changes over time to detect analyst sentiment shifts

from __future__ import annotations

import os
from datetime import date
from typing import Any

import structlog

from .resilience import api_get, with_retry

logger = structlog.get_logger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"


def _finnhub_headers() -> dict[str, str]:
    key = os.environ.get("FINNHUB_API_KEY", "")
    return {"X-Finnhub-Token": key}


@with_retry(source="finnhub", max_retries=2, base_delay=1.0)
async def fetch_earnings_estimates(symbol: str) -> list[dict[str, Any]]:
    if not os.environ.get("FINNHUB_API_KEY"):
        return []
    url = f"{FINNHUB_BASE}/stock/eps-estimate"
    params = {"symbol": symbol, "freq": "quarterly"}
    resp = await api_get(url, headers=_finnhub_headers(), params=params, source="finnhub")
    data = resp.json()
    estimates = data.get("data", [])
    result: list[dict[str, Any]] = []
    for est in estimates:
        result.append({
            "symbol": symbol,
            "period": est.get("period", ""),
            "eps_avg": est.get("epsAvg"),
            "eps_high": est.get("epsHigh"),
            "eps_low": est.get("epsLow"),
            "number_analysts": est.get("numberAnalysts", 0),
            "revenue_avg": est.get("revenueAvg"),
        })
    logger.info("earnings_estimates_fetched", symbol=symbol, count=len(result))
    return result


def compute_revision_momentum(estimates: list[dict[str, Any]]) -> float:
    if len(estimates) < 2:
        return 0.0
    current = estimates[0].get("eps_avg")
    previous = estimates[-1].get("eps_avg")
    if current is None or previous is None:
        return 0.0
    if abs(previous) < 0.001:
        return 0.0
    momentum = (current - previous) / abs(previous)
    return max(-1.0, min(1.0, momentum))


async def store_earnings_estimates(pool: Any, estimates: list[dict[str, Any]]) -> int:
    if not estimates:
        return 0
    inserted = 0
    async with pool.acquire() as conn:
        for est in estimates:
            try:
                await conn.execute(
                    """
                    INSERT INTO earnings_revisions
                        (symbol, period, estimate_date, eps_estimate, revenue_estimate)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (symbol, period, estimate_date) DO UPDATE SET
                        eps_estimate = EXCLUDED.eps_estimate,
                        revenue_estimate = EXCLUDED.revenue_estimate
                    """,
                    est["symbol"], est["period"], date.today(),
                    est.get("eps_avg"), est.get("revenue_avg"),
                )
                inserted += 1
            except Exception as exc:
                logger.warning("earnings_revision_insert_failed", error=str(exc))
    return inserted

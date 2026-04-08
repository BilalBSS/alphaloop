# / corporate events: earnings calendar, dividends, days to earnings
# / finnhub calendar + yfinance as fallback

from __future__ import annotations

import asyncio
import json
import os
from datetime import date, timedelta
from typing import Any

import structlog

from .resilience import api_get, with_retry

logger = structlog.get_logger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"


def _finnhub_headers() -> dict[str, str]:
    key = os.environ.get("FINNHUB_API_KEY", "")
    return {"X-Finnhub-Token": key}


@with_retry(source="finnhub", max_retries=2, base_delay=1.0)
async def fetch_earnings_calendar(symbol: str) -> dict[str, Any] | None:
    if not os.environ.get("FINNHUB_API_KEY"):
        return None
    today = date.today()
    url = f"{FINNHUB_BASE}/calendar/earnings"
    params = {
        "symbol": symbol,
        "from": today.isoformat(),
        "to": (today + timedelta(days=90)).isoformat(),
    }
    resp = await api_get(url, headers=_finnhub_headers(), params=params, source="finnhub")
    data = resp.json()
    events = data.get("earningsCalendar", [])
    if not events:
        return None
    # / return the nearest upcoming earnings
    nearest = events[0]
    return {
        "symbol": symbol,
        "date": nearest.get("date", ""),
        "eps_estimate": nearest.get("epsEstimate"),
        "revenue_estimate": nearest.get("revenueEstimate"),
        "hour": nearest.get("hour", ""),
    }


@with_retry(source="finnhub", max_retries=2, base_delay=1.0)
async def fetch_dividends(symbol: str) -> list[dict[str, Any]]:
    if not os.environ.get("FINNHUB_API_KEY"):
        return []
    today = date.today()
    url = f"{FINNHUB_BASE}/stock/dividend2"
    params = {
        "symbol": symbol,
        "from": (today - timedelta(days=365)).isoformat(),
        "to": (today + timedelta(days=90)).isoformat(),
    }
    resp = await api_get(url, headers=_finnhub_headers(), params=params, source="finnhub")
    data = resp.json()
    if not isinstance(data, list):
        return []
    result: list[dict[str, Any]] = []
    for d in data:
        result.append({
            "symbol": symbol,
            "date": d.get("payDate", d.get("date", "")),
            "amount": d.get("amount"),
            "currency": d.get("currency", "USD"),
        })
    return result


def _fetch_yf_calendar_sync(symbol: str) -> dict[str, Any] | None:
    # / yfinance fallback for earnings date
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar
        if cal is None or (hasattr(cal, "empty") and cal.empty):
            return None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if ed:
                return {"date": str(ed[0]) if isinstance(ed, list) else str(ed)}
        return None
    except Exception:
        return None


async def days_to_earnings(symbol: str) -> int | None:
    # / try finnhub first, fallback to yfinance
    try:
        cal = await fetch_earnings_calendar(symbol)
        if cal and cal.get("date"):
            earnings_date = date.fromisoformat(cal["date"])
            delta = (earnings_date - date.today()).days
            return delta if delta >= 0 else None
    except Exception:
        pass
    try:
        yf_cal = await asyncio.to_thread(_fetch_yf_calendar_sync, symbol)
        if yf_cal and yf_cal.get("date"):
            earnings_date = date.fromisoformat(yf_cal["date"][:10])
            delta = (earnings_date - date.today()).days
            return delta if delta >= 0 else None
    except Exception:
        pass
    return None


async def store_corporate_event(pool: Any, symbol: str, event_type: str, event_date: date, details: dict[str, Any] | None = None) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO corporate_events (symbol, event_type, event_date, details)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (symbol, event_type, event_date) DO UPDATE SET
                details = EXCLUDED.details
            """,
            symbol, event_type, event_date,
            json.dumps(details) if details else None,
        )

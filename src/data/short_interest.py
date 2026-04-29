
from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Any

import httpx
import structlog

from .resilience import api_get, configure_rate_limit, with_retry

logger = structlog.get_logger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"
FINRA_BASE = "https://cdn.finra.org/equity/regsho/daily"

configure_rate_limit("finra", max_concurrent=2, delay=1.0)


def _finnhub_headers() -> dict[str, str]:
    key = os.environ.get("FINNHUB_API_KEY", "")
    return {"X-Finnhub-Token": key}


async def _fetch_short_yfinance(symbol: str) -> dict[str, Any] | None:
    def _fetch():
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        short_pct = info.get("shortPercentOfFloat")
        if short_pct is None:
            return None
        return {
            "symbol": symbol,
            "date": str(date.today()),
            "short_volume": int(info.get("sharesShort", 0)),
            "total_volume": int(info.get("averageVolume", 0)),
            "short_ratio": float(info.get("shortRatio", 0) or 0),
            "short_percent_float": float(short_pct),
        }
    return await asyncio.to_thread(_fetch)


@with_retry(source="finnhub", max_retries=2, base_delay=1.0)
async def _fetch_short_finnhub(symbol: str) -> dict[str, Any] | None:
    if not os.environ.get("FINNHUB_API_KEY"):
        return None
    url = f"{FINNHUB_BASE}/stock/short-interest"
    params = {"symbol": symbol}
    try:
        resp = await api_get(url, headers=_finnhub_headers(), params=params, source="finnhub")
        data = resp.json()
        entries = data.get("data", [])
        if not entries:
            return None
        latest = entries[0]
        return {
            "symbol": symbol,
            "date": latest.get("settlementDate", ""),
            "short_interest": latest.get("shortInterest", 0),
        }
    except Exception as exc:
        logger.debug("finnhub_short_interest_failed", symbol=symbol, error=str(exc))
        return None


async def fetch_short_interest(symbol: str) -> dict[str, Any] | None:
    try:
        result = await _fetch_short_yfinance(symbol)
        if result:
            return result
    except Exception as exc:
        logger.debug("yfinance_short_failed", symbol=symbol, error=str(exc))
    # / fallback: finnhub
    try:
        result = await _fetch_short_finnhub(symbol)
        if result:
            return result
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    # / fallback: finra
    return await fetch_finra_short_volume(symbol)


@with_retry(source="finra", max_retries=2, base_delay=2.0)
async def fetch_finra_short_volume(symbol: str, target_date: date | None = None) -> dict[str, Any] | None:
    d = target_date or date.today()
    url = f"{FINRA_BASE}/CNMSshvol{d.strftime('%Y%m%d')}.txt"
    try:
        resp = await api_get(url, source="finra")
        text = resp.text
        for line in text.strip().split("\n"):
            parts = line.split("|")
            if len(parts) >= 5 and parts[1].strip().upper() == symbol.upper():
                short_vol = int(parts[2].strip()) if parts[2].strip().isdigit() else 0
                total_vol = int(parts[4].strip()) if parts[4].strip().isdigit() else 0
                short_pct = short_vol / total_vol if total_vol > 0 else 0.0
                return {
                    "symbol": symbol,
                    "date": d.isoformat(),
                    "short_volume": short_vol,
                    "total_volume": total_vol,
                    "short_ratio": round(short_pct, 4),
                }
    except Exception as exc:
        logger.debug("finra_short_volume_failed", symbol=symbol, error=str(exc))
    return None


async def store_short_interest(pool: Any, data: dict[str, Any]) -> None:
    if not data:
        return
    d = data.get("date")
    if isinstance(d, str):
        d = date.fromisoformat(d)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO short_interest (symbol, date, short_volume, total_volume, short_ratio)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (symbol, date) DO UPDATE SET
                short_volume = EXCLUDED.short_volume,
                total_volume = EXCLUDED.total_volume,
                short_ratio = EXCLUDED.short_ratio
            """,
            data["symbol"], d,
            data.get("short_volume"), data.get("total_volume"),
            data.get("short_ratio"),
        )

# / congressional trading: senate stock watcher (free) + finnhub fallback
# / tracks buy/sell activity by congress members per symbol

from __future__ import annotations

import os
from datetime import date
from typing import Any

import httpx
import structlog

from .resilience import api_get, with_retry

logger = structlog.get_logger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"
SENATE_S3_URL = "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json"

# / module-level cache: fetched once per cycle
_senate_cache: dict[str, Any] = {"data": None, "date": None}


def _finnhub_headers() -> dict[str, str]:
    key = os.environ.get("FINNHUB_API_KEY", "")
    return {"X-Finnhub-Token": key}


async def _fetch_senate_bulk() -> list[dict[str, Any]]:
    # / fetch full senate transaction dump, cache per day
    today = date.today().isoformat()
    if _senate_cache["data"] is not None and _senate_cache["date"] == today:
        return _senate_cache["data"]
    try:
        resp = await api_get(SENATE_S3_URL, source="senate_s3", timeout=60.0)
        data = resp.json()
        if isinstance(data, list):
            _senate_cache["data"] = data
            _senate_cache["date"] = today
            logger.info("senate_bulk_fetched", count=len(data))
            return data
    except Exception as exc:
        logger.warning("senate_bulk_fetch_failed", error=str(exc))
    return []


async def _fetch_senate_trades(symbol: str) -> list[dict[str, Any]]:
    # / filter cached senate data for symbol
    bulk = await _fetch_senate_bulk()
    if not bulk:
        return []
    trades: list[dict[str, Any]] = []
    sym_upper = symbol.upper()
    for item in bulk:
        ticker = (item.get("ticker") or "").upper()
        if ticker != sym_upper:
            continue
        trades.append({
            "symbol": symbol,
            "filing_date": item.get("transaction_date") or item.get("disclosure_date", ""),
            "name": (item.get("senator") or item.get("full_name") or "")[:200],
            "transaction_type": item.get("type") or item.get("transaction_type", ""),
            "amount_range": item.get("amount") or item.get("amount_range", ""),
        })
    # / sort by date descending, return last 50
    trades.sort(key=lambda t: t.get("filing_date", ""), reverse=True)
    return trades[:50]


@with_retry(source="finnhub", max_retries=2, base_delay=1.0)
async def _fetch_finnhub_congressional(symbol: str) -> list[dict[str, Any]]:
    if not os.environ.get("FINNHUB_API_KEY"):
        return []
    url = f"{FINNHUB_BASE}/stock/congressional-trading"
    params = {"symbol": symbol}
    resp = await api_get(url, headers=_finnhub_headers(), params=params, source="finnhub")
    data = resp.json()
    raw = data.get("data", [])
    trades: list[dict[str, Any]] = []
    for item in raw:
        trades.append({
            "symbol": symbol,
            "filing_date": item.get("transactionDate", ""),
            "name": (item.get("name") or "")[:200],
            "transaction_type": item.get("transactionType", ""),
            "amount_range": item.get("amountRange", ""),
        })
    logger.info("congressional_trades_fetched_finnhub", symbol=symbol, count=len(trades))
    return trades


async def fetch_congressional_trades(symbol: str) -> list[dict[str, Any]]:
    # / primary: senate stock watcher s3 (free, no auth)
    # / fallback: finnhub (if api key available and premium)
    trades = await _fetch_senate_trades(symbol)
    if trades:
        return trades
    # / fallback to finnhub
    if not os.environ.get("FINNHUB_API_KEY"):
        return []
    try:
        return await _fetch_finnhub_congressional(symbol)
    except (httpx.HTTPError, ValueError, KeyError):
        return []


async def store_congressional_trades(pool: Any, trades: list[dict[str, Any]]) -> int:
    if not trades:
        return 0
    inserted = 0
    async with pool.acquire() as conn:
        for t in trades:
            try:
                filing_date = t["filing_date"]
                if isinstance(filing_date, str) and filing_date:
                    filing_date = date.fromisoformat(filing_date)
                elif not isinstance(filing_date, date):
                    continue
                await conn.execute(
                    """
                    INSERT INTO congressional_trades
                        (symbol, filing_date, name, transaction_type, amount_range)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (symbol, filing_date, name, transaction_type) DO NOTHING
                    """,
                    t["symbol"], filing_date, t["name"],
                    t["transaction_type"], t["amount_range"],
                )
                inserted += 1
            except Exception as exc:
                logger.warning("congressional_insert_failed", error=str(exc))
    return inserted


def compute_net_buy_ratio(trades: list[dict[str, Any]]) -> float:
    # / ratio of buys vs sells: 1.0 = all buys, -1.0 = all sells
    if not trades:
        return 0.0
    buys = sum(1 for t in trades if "purchase" in (t.get("transaction_type") or "").lower())
    sells = sum(1 for t in trades if "sale" in (t.get("transaction_type") or "").lower())
    total = buys + sells
    if total == 0:
        return 0.0
    return (buys - sells) / total

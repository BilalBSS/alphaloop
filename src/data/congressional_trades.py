# / congressional trading: finnhub congressional trading data
# / tracks buy/sell activity by congress members per symbol

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
async def fetch_congressional_trades(symbol: str) -> list[dict[str, Any]]:
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
    logger.info("congressional_trades_fetched", symbol=symbol, count=len(trades))
    return trades


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

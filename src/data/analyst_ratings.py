# / analyst ratings: yfinance (free) + finnhub fallback
# / computes consensus score and target upside

from __future__ import annotations

import asyncio
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
async def _fetch_recommendations(symbol: str) -> list[dict[str, Any]]:
    if not os.environ.get("FINNHUB_API_KEY"):
        return []
    url = f"{FINNHUB_BASE}/stock/recommendation"
    params = {"symbol": symbol}
    resp = await api_get(url, headers=_finnhub_headers(), params=params, source="finnhub")
    return resp.json()


@with_retry(source="finnhub", max_retries=2, base_delay=1.0)
async def _fetch_price_target(symbol: str) -> dict[str, Any]:
    if not os.environ.get("FINNHUB_API_KEY"):
        return {}
    url = f"{FINNHUB_BASE}/stock/price-target"
    params = {"symbol": symbol}
    resp = await api_get(url, headers=_finnhub_headers(), params=params, source="finnhub")
    return resp.json()


async def _fetch_analyst_yfinance(symbol: str) -> dict[str, Any]:
    # / yfinance recommendations + price targets (free, no auth)
    def _fetch():
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        result = {}
        # / recommendations
        recs = ticker.recommendations
        if recs is not None and not recs.empty:
            latest = recs.iloc[-1]
            result["strongBuy"] = int(latest.get("strongBuy", 0))
            result["buy"] = int(latest.get("buy", 0))
            result["hold"] = int(latest.get("hold", 0))
            result["sell"] = int(latest.get("sell", 0))
            result["strongSell"] = int(latest.get("strongSell", 0))
        # / price targets
        targets = ticker.analyst_price_targets
        if targets:
            result["targetHigh"] = targets.get("high")
            result["targetLow"] = targets.get("low")
            result["targetMean"] = targets.get("mean")
            result["targetMedian"] = targets.get("median")
            result["currentPrice"] = targets.get("current")
        return result
    return await asyncio.to_thread(_fetch)


def compute_consensus_score(rec: dict[str, Any]) -> float:
    # / weighted average: strong_buy=1.0, buy=0.5, hold=0, sell=-0.5, strong_sell=-1.0
    sb = rec.get("strongBuy", 0) or 0
    b = rec.get("buy", 0) or 0
    h = rec.get("hold", 0) or 0
    s = rec.get("sell", 0) or 0
    ss = rec.get("strongSell", 0) or 0
    total = sb + b + h + s + ss
    if total == 0:
        return 0.0
    weighted = (sb * 1.0 + b * 0.5 + h * 0.0 + s * -0.5 + ss * -1.0)
    return max(-1.0, min(1.0, weighted / total))


def compute_target_upside(target_mean: float | None, current_price: float | None) -> float | None:
    if target_mean is None or current_price is None or current_price <= 0:
        return None
    return (target_mean - current_price) / current_price


async def fetch_analyst_ratings(symbol: str) -> dict[str, Any]:
    # / primary: yfinance (free, no auth)
    try:
        data = await _fetch_analyst_yfinance(symbol)
        if data and ("strongBuy" in data or "targetMean" in data):
            result: dict[str, Any] = {"symbol": symbol, "source": "yfinance"}
            if "strongBuy" in data:
                result["strong_buy"] = data["strongBuy"]
                result["buy"] = data.get("buy", 0)
                result["hold"] = data.get("hold", 0)
                result["sell"] = data.get("sell", 0)
                result["strong_sell"] = data.get("strongSell", 0)
                result["consensus_score"] = compute_consensus_score(data)
            if "targetMean" in data:
                result["target_high"] = data.get("targetHigh")
                result["target_low"] = data.get("targetLow")
                result["target_mean"] = data.get("targetMean")
            logger.info("analyst_ratings_fetched", symbol=symbol, source="yfinance")
            return result
    except Exception as exc:
        logger.debug("yfinance_analyst_failed", symbol=symbol, error=str(exc))

    # / fallback: finnhub
    result: dict[str, Any] = {"symbol": symbol, "source": "finnhub"}
    try:
        recs = await _fetch_recommendations(symbol)
        if recs:
            latest = recs[0]
            result["strong_buy"] = latest.get("strongBuy", 0)
            result["buy"] = latest.get("buy", 0)
            result["hold"] = latest.get("hold", 0)
            result["sell"] = latest.get("sell", 0)
            result["strong_sell"] = latest.get("strongSell", 0)
            result["consensus_score"] = compute_consensus_score(latest)
            result["period"] = latest.get("period", "")
    except Exception as exc:
        logger.warning("analyst_recs_failed", symbol=symbol, error=str(exc))

    try:
        targets = await _fetch_price_target(symbol)
        if targets:
            result["target_high"] = targets.get("targetHigh")
            result["target_low"] = targets.get("targetLow")
            result["target_mean"] = targets.get("targetMean")
    except Exception as exc:
        logger.warning("analyst_targets_failed", symbol=symbol, error=str(exc))

    logger.info("analyst_ratings_fetched", symbol=symbol, source="finnhub")
    return result


async def store_analyst_ratings(pool: Any, symbol: str, data: dict[str, Any]) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO analyst_ratings
                (symbol, date, strong_buy, buy, hold, sell, strong_sell,
                 target_high, target_low, target_mean)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (symbol, date) DO UPDATE SET
                strong_buy = EXCLUDED.strong_buy, buy = EXCLUDED.buy,
                hold = EXCLUDED.hold, sell = EXCLUDED.sell,
                strong_sell = EXCLUDED.strong_sell,
                target_high = EXCLUDED.target_high,
                target_low = EXCLUDED.target_low,
                target_mean = EXCLUDED.target_mean
            """,
            symbol, date.today(),
            data.get("strong_buy", 0), data.get("buy", 0),
            data.get("hold", 0), data.get("sell", 0),
            data.get("strong_sell", 0),
            data.get("target_high"), data.get("target_low"),
            data.get("target_mean"),
        )

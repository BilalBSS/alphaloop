# / news sentiment: finnhub company news + keyword scoring fallback
# / stores to existing news_sentiment table

from __future__ import annotations

import os
import re
from datetime import date, timedelta
from typing import Any

import structlog

from .resilience import api_get, configure_rate_limit, with_retry

logger = structlog.get_logger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"

configure_rate_limit("finnhub", max_concurrent=5, delay=0.5)

# / keyword scoring fallback when finnhub sentiment isn't available
_POSITIVE_KEYWORDS = {
    "beat", "exceed", "record", "growth", "upgrade", "buy",
    "outperform", "bullish", "strong", "surge", "rally", "profit",
    "revenue growth", "raised guidance", "beat estimates",
}
_NEGATIVE_KEYWORDS = {
    "miss", "downgrade", "sell", "underperform", "bearish", "weak",
    "decline", "crash", "loss", "layoff", "recall", "investigation",
    "lowered guidance", "missed estimates", "warning",
}


def _keyword_score(text: str) -> float:
    # / simple keyword-based sentiment: +1 to -1
    text_lower = text.lower()
    pos = sum(1 for kw in _POSITIVE_KEYWORDS if kw in text_lower)
    neg = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in text_lower)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / total


def _finnhub_headers() -> dict[str, str]:
    key = os.environ.get("FINNHUB_API_KEY", "")
    return {"X-Finnhub-Token": key}


@with_retry(source="finnhub", max_retries=2, base_delay=1.0)
async def fetch_company_news(
    symbol: str, days: int = 7,
) -> list[dict[str, Any]]:
    # / fetch recent news from finnhub
    if not os.environ.get("FINNHUB_API_KEY"):
        return []

    end = date.today()
    start = end - timedelta(days=days)

    url = f"{FINNHUB_BASE}/company-news"
    params = {
        "symbol": symbol,
        "from": start.isoformat(),
        "to": end.isoformat(),
    }
    resp = await api_get(url, headers=_finnhub_headers(), params=params, source="finnhub")
    return resp.json()


@with_retry(source="finnhub", max_retries=2, base_delay=1.0)
async def fetch_news_sentiment(symbol: str) -> dict[str, Any] | None:
    # / fetch finnhub's built-in sentiment score for a symbol
    if not os.environ.get("FINNHUB_API_KEY"):
        return None

    url = f"{FINNHUB_BASE}/news-sentiment"
    params = {"symbol": symbol}
    resp = await api_get(url, headers=_finnhub_headers(), params=params, source="finnhub")
    data = resp.json()

    sentiment = data.get("sentiment")
    if not sentiment:
        return None

    return {
        "symbol": symbol,
        "bullish_percent": sentiment.get("bullishPercent", 0.5),
        "bearish_percent": sentiment.get("bearishPercent", 0.5),
        "articles_in_last_week": data.get("buzz", {}).get("articlesInLastWeek", 0),
        "buzz": data.get("buzz", {}).get("buzz", 0),
        "sector_avg_bullish": data.get("sectorAverageBullishPercent", 0.5),
    }


async def compute_sentiment_score(
    symbol: str, days: int = 7,
) -> float:
    # / compute aggregate sentiment score for a symbol
    # / tries finnhub built-in first, falls back to keyword scoring
    try:
        sentiment = await fetch_news_sentiment(symbol)
        if sentiment:
            bullish = sentiment["bullish_percent"]
            bearish = sentiment["bearish_percent"]
            # / scale to -1 to +1
            return bullish - bearish
    except Exception:
        pass

    # / fallback: keyword scoring on news headlines
    try:
        articles = await fetch_company_news(symbol, days=days)
        if not articles:
            return 0.0
        scores = [_keyword_score(a.get("headline", "")) for a in articles]
        return sum(scores) / len(scores) if scores else 0.0
    except Exception:
        return 0.0


async def store_sentiment(pool, symbol: str, score: float, source: str = "finnhub") -> None:
    # / store to existing news_sentiment table
    label = "positive" if score > 0.1 else "negative" if score < -0.1 else "neutral"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO news_sentiment (symbol, date, sentiment_score, sentiment_label, source, url)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (symbol, date, url) DO UPDATE SET
                sentiment_score = EXCLUDED.sentiment_score,
                sentiment_label = EXCLUDED.sentiment_label
            """,
            symbol,
            date.today(),
            score,
            label,
            source,
            f"aggregate:{source}",
        )

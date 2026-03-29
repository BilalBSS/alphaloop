# / news sentiment: finnhub company news + groq llm scoring
# / groq scores headlines for free, keyword scoring as fallback
# / stores to existing news_sentiment table

from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from typing import Any

import structlog

from .resilience import api_get, configure_rate_limit, with_retry

logger = structlog.get_logger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"

configure_rate_limit("finnhub", max_concurrent=5, delay=0.5)

# / keyword scoring fallback when groq is unavailable
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


async def _groq_score_headlines(headlines: list[str]) -> float | None:
    # / use groq llm to score a batch of headlines, returns -1.0 to 1.0
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key or not headlines:
        return None

    batch = "\n".join(f"- {h}" for h in headlines[:15])
    prompt = (
        f"Rate the overall sentiment of these news headlines on a scale from "
        f"-1.0 (very bearish) to 1.0 (very bullish). Consider financial impact.\n\n"
        f"{batch}\n\n"
        f"Respond with ONLY a single number between -1.0 and 1.0, nothing else."
    )

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 10,
                    "temperature": 0.1,
                },
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
            # / extract first number from response in case llm adds words
            match = re.search(r"-?\d+\.?\d*", text)
            if not match:
                return None
            score = float(match.group())
            return max(-1.0, min(1.0, score))
    except Exception as exc:
        logger.debug("groq_sentiment_failed", error=str(exc))
        return None


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


async def compute_sentiment_score(
    symbol: str, days: int = 7,
) -> float:
    # / fetch headlines from finnhub /company-news, score via groq llm
    # / falls back to keyword scoring if groq unavailable
    try:
        articles = await fetch_company_news(symbol, days=days)
        if not articles:
            return 0.0

        headlines = [a.get("headline", "") for a in articles if a.get("headline")]
        if not headlines:
            return 0.0

        # / try groq llm scoring first
        groq_score = await _groq_score_headlines(headlines)
        if groq_score is not None:
            return groq_score

        # / fallback: keyword scoring
        scores = [_keyword_score(h) for h in headlines]
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

# / social sentiment: stocktwits bullish/bearish + fear & greed index
# / reddit placeholder for future oauth setup
# / stores to social_sentiment table, computes aggregate -1.0 to 1.0

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import structlog

from .resilience import api_get, configure_rate_limit, with_retry

logger = structlog.get_logger(__name__)

STOCKTWITS_BASE = "https://api.stocktwits.com/api/2"
FNG_URL = "https://api.alternative.me/fng/"

configure_rate_limit("stocktwits", max_concurrent=3, delay=1.0)
configure_rate_limit("fng", max_concurrent=2, delay=0.5)


@with_retry(source="stocktwits", max_retries=2, base_delay=2.0)
async def fetch_stocktwits_sentiment(symbol: str) -> dict[str, Any] | None:
    # / fetch bullish/bearish ratio and volume from stocktwits
    url = f"{STOCKTWITS_BASE}/streams/symbol/{symbol}.json"
    try:
        resp = await api_get(url, source="stocktwits")
        data = resp.json()

        sentiments = data.get("symbol", {}).get("sentiments")
        if not sentiments:
            logger.debug("stocktwits_no_sentiment", symbol=symbol)
            return None

        bullish = sentiments.get("bullish", 0)
        bearish = sentiments.get("bearish", 0)
        total = bullish + bearish

        if total == 0:
            return None

        messages = data.get("messages", [])

        return {
            "bullish_pct": bullish / total,
            "bearish_pct": bearish / total,
            "volume": len(messages),
            "raw_score": (bullish - bearish) / total,
        }
    except Exception as exc:
        logger.debug("stocktwits_fetch_failed", symbol=symbol, error=str(exc))
        return None


@with_retry(source="fng", max_retries=2, base_delay=1.0)
async def fetch_fear_greed_index() -> dict[str, Any] | None:
    # / fetch fear & greed index, normalize 0-100 to -1.0 to 1.0
    try:
        resp = await api_get(FNG_URL, source="fng")
        data = resp.json()

        value_str = data.get("data", [{}])[0].get("value")
        if value_str is None:
            logger.debug("fng_no_value")
            return None

        value = float(value_str)
        # / 0 = extreme fear = -1.0, 50 = neutral = 0.0, 100 = extreme greed = 1.0
        normalized = (value - 50.0) / 50.0

        return {
            "raw_value": value,
            "normalized": max(-1.0, min(1.0, normalized)),
        }
    except Exception as exc:
        logger.debug("fng_fetch_failed", error=str(exc))
        return None


async def fetch_reddit_sentiment(symbol: str) -> dict[str, Any] | None:
    # / placeholder: needs oauth setup
    # / TODO: implement reddit sentiment via praw or async praw
    logger.debug("reddit_sentiment_not_implemented", symbol=symbol)
    return None


async def store_social_sentiment(
    pool: Any,
    symbol: str,
    source: str,
    bullish_pct: float | None,
    bearish_pct: float | None,
    volume: int | None,
    raw_score: float | None,
) -> None:
    # / upsert to social_sentiment table
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO social_sentiment (symbol, date, source, bullish_pct, bearish_pct, volume, raw_score)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (symbol, date, source) DO UPDATE SET
                bullish_pct = EXCLUDED.bullish_pct,
                bearish_pct = EXCLUDED.bearish_pct,
                volume = EXCLUDED.volume,
                raw_score = EXCLUDED.raw_score
            """,
            symbol,
            date.today(),
            source,
            bullish_pct,
            bearish_pct,
            volume,
            raw_score,
        )


async def compute_social_score(symbol: str) -> float:
    # / aggregate social sentiment from all sources, returns -1.0 to 1.0
    scores: list[float] = []
    weights: list[float] = []

    # / stocktwits: direct sentiment ratio
    st_data = await fetch_stocktwits_sentiment(symbol)
    if st_data and st_data.get("raw_score") is not None:
        scores.append(st_data["raw_score"])
        weights.append(0.6)

    # / fear & greed: market-wide sentiment
    fng_data = await fetch_fear_greed_index()
    if fng_data and fng_data.get("normalized") is not None:
        scores.append(fng_data["normalized"])
        weights.append(0.4)

    if not scores:
        return 0.0

    total_weight = sum(weights)
    weighted = sum(s * w for s, w in zip(scores, weights)) / total_weight
    return max(-1.0, min(1.0, weighted))


async def run_social_sentiment(
    pool: Any,
    symbols: list[str],
) -> dict[str, float]:
    # / run social sentiment pipeline for all symbols
    results: dict[str, float] = {}

    # / fetch fear & greed once (market-wide, not per-symbol)
    fng_data = await fetch_fear_greed_index()

    for symbol in symbols:
        try:
            # / stocktwits per-symbol
            st_data = await fetch_stocktwits_sentiment(symbol)

            if st_data:
                await store_social_sentiment(
                    pool, symbol, "stocktwits",
                    st_data["bullish_pct"],
                    st_data["bearish_pct"],
                    st_data["volume"],
                    st_data["raw_score"],
                )

            # / store fear & greed per symbol for easy querying
            if fng_data:
                await store_social_sentiment(
                    pool, symbol, "fear_greed",
                    None, None, None,
                    fng_data["normalized"],
                )

            # / compute aggregate score
            scores: list[float] = []
            weights: list[float] = []

            if st_data and st_data.get("raw_score") is not None:
                scores.append(st_data["raw_score"])
                weights.append(0.6)

            if fng_data and fng_data.get("normalized") is not None:
                scores.append(fng_data["normalized"])
                weights.append(0.4)

            if scores:
                total_weight = sum(weights)
                results[symbol] = max(-1.0, min(1.0,
                    sum(s * w for s, w in zip(scores, weights)) / total_weight
                ))
            else:
                results[symbol] = 0.0

            logger.info("social_sentiment_processed", symbol=symbol, score=results[symbol])
        except Exception as exc:
            logger.warning("social_sentiment_failed", symbol=symbol, error=str(exc))
            results[symbol] = 0.0

    return results

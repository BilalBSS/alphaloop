# / social sentiment: apewisdom (reddit mentions) + fear & greed + vix
# / stores to social_sentiment table, computes aggregate -1.0 to 1.0

from __future__ import annotations

import asyncio
import math
from datetime import date
from typing import Any

import structlog

from .resilience import api_get, configure_rate_limit, with_retry
from src.notifications.notifier import notify_sentiment_shift

logger = structlog.get_logger(__name__)

APEWISDOM_BASE = "https://apewisdom.io/api/v1.0"
STOCKTWITS_BASE = "https://api.stocktwits.com/api/2"
FNG_URL = "https://api.alternative.me/fng/"

from .symbols import is_crypto

configure_rate_limit("apewisdom", max_concurrent=2, delay=1.0)
configure_rate_limit("stocktwits", max_concurrent=3, delay=1.0)
configure_rate_limit("fng", max_concurrent=2, delay=0.5)


# ---------------------------------------------------------------------------
# / apewisdom: reddit mention tracking (no api key needed)
# ---------------------------------------------------------------------------

@with_retry(source="apewisdom", max_retries=2, base_delay=2.0)
async def fetch_apewisdom(filter_type: str = "all-stocks") -> dict[str, dict[str, Any]]:
    # / fetch trending tickers from reddit via apewisdom
    # / returns dict keyed by ticker with mentions, upvotes, rank, raw_score
    result: dict[str, dict[str, Any]] = {}
    try:
        resp = await api_get(
            f"{APEWISDOM_BASE}/filter/{filter_type}/page/1",
            source="apewisdom",
        )
        data = resp.json()
        items = data.get("results", [])
        if not items:
            logger.info("apewisdom_empty_results", filter=filter_type,
                        keys=list(data.keys())[:5], count=data.get("count", 0),
                        status=resp.status_code, body_len=len(resp.text))
            return result

        max_mentions = max((r.get("mentions", 1) for r in items), default=1)

        for r in items:
            ticker = (r.get("ticker") or "").upper()
            if not ticker:
                continue
            mentions = r.get("mentions", 0)
            upvotes = r.get("upvotes", 0)
            rank = r.get("rank", 999)
            # / log-scaled buzz: top ticker ~1.0, tail ~0.2-0.4
            raw = math.log1p(mentions) / math.log1p(max_mentions) if max_mentions > 0 else 0.0
            result[ticker] = {
                "mentions": mentions,
                "upvotes": upvotes,
                "rank": rank,
                "raw_score": min(1.0, raw),
            }

        logger.info("apewisdom_fetched", filter=filter_type, count=len(result))
    except Exception as exc:
        logger.warning("apewisdom_fetch_failed", filter=filter_type, error=str(exc)[:200])
    return result


# ---------------------------------------------------------------------------
# / stocktwits (kept as fallback, api registrations currently paused)
# ---------------------------------------------------------------------------

@with_retry(source="stocktwits", max_retries=2, base_delay=2.0)
async def fetch_stocktwits_sentiment(symbol: str) -> dict[str, Any] | None:
    # / fetch bullish/bearish ratio and volume from stocktwits
    # / crypto uses BTC.X format on stocktwits, not BTC-USD
    st_symbol = symbol.replace("-USD", ".X") if symbol.endswith("-USD") else symbol
    url = f"{STOCKTWITS_BASE}/streams/symbol/{st_symbol}.json"
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


# ---------------------------------------------------------------------------
# / fear & greed index (crypto)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# / vix (equity fear gauge)
# ---------------------------------------------------------------------------

def _fetch_vix_sync() -> float | None:
    # / yfinance is sync, runs in thread pool
    try:
        import yfinance as yf
        ticker = yf.Ticker("^VIX")
        hist = ticker.history(period="1d")
        if hist.empty:
            return None
        vix = float(hist["Close"].iloc[-1])
        # / 10 = extreme greed (1.0), 30 = neutral (0.0), 50 = extreme fear (-1.0)
        return max(-1.0, min(1.0, (30.0 - vix) / 20.0))
    except Exception:
        return None


async def fetch_vix() -> float | None:
    # / VIX fear gauge for equities, wrapped to avoid blocking event loop
    try:
        result = await asyncio.to_thread(_fetch_vix_sync)
        if result is not None:
            logger.info("vix_fetched", normalized=result)
        return result
    except Exception as exc:
        logger.debug("vix_fetch_failed", error=str(exc))
        return None


async def fetch_reddit_sentiment(symbol: str) -> dict[str, Any] | None:
    # / deprecated: use fetch_apewisdom instead
    logger.debug("reddit_sentiment_deprecated_use_apewisdom", symbol=symbol)
    return None


# ---------------------------------------------------------------------------
# / storage + scoring
# ---------------------------------------------------------------------------

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

    # / fetch market-wide gauges once (not per-symbol)
    fng_data = await fetch_fear_greed_index()  # / crypto fear & greed
    vix_score = await fetch_vix()              # / equity VIX-based fear gauge

    # / fetch apewisdom trending lists once (stocks + crypto)
    aw_stocks = await fetch_apewisdom("all-stocks")
    aw_crypto = await fetch_apewisdom("all-crypto")

    for symbol in symbols:
        try:
            # / apewisdom per-symbol lookup (replaces stocktwits)
            if is_crypto(symbol):
                aw_ticker = symbol.replace("-USD", "")
                aw_data = aw_crypto.get(aw_ticker)
            else:
                aw_data = aw_stocks.get(symbol)

            if aw_data:
                await store_social_sentiment(
                    pool, symbol, "apewisdom",
                    None, None,
                    aw_data["mentions"],
                    aw_data["raw_score"],
                )

            # / store fear gauge per symbol — VIX for equity, FNG for crypto
            if is_crypto(symbol):
                if fng_data:
                    await store_social_sentiment(
                        pool, symbol, "fear_greed",
                        None, None, None,
                        fng_data["normalized"],
                    )
                fear_score = fng_data["normalized"] if fng_data else None
            else:
                if vix_score is not None:
                    await store_social_sentiment(
                        pool, symbol, "vix",
                        None, None, None,
                        vix_score,
                    )
                fear_score = vix_score

            # / compute aggregate score
            # / social buzz 60% + fear gauge 40%
            scores: list[float] = []
            weights: list[float] = []

            if aw_data and aw_data.get("raw_score") is not None:
                scores.append(aw_data["raw_score"])
                weights.append(0.6)

            if fear_score is not None:
                scores.append(fear_score)
                weights.append(0.4)

            if scores:
                total_weight = sum(weights)
                results[symbol] = max(-1.0, min(1.0,
                    sum(s * w for s, w in zip(scores, weights)) / total_weight
                ))
            else:
                results[symbol] = 0.0

            # / notify on large fear gauge swings (>0.3 delta, same source)
            try:
                fear_source = "vix" if not is_crypto(symbol) else "fear_greed"
                if fear_score is not None:
                    async with pool.acquire() as conn:
                        prev = await conn.fetchval(
                            """SELECT raw_score FROM social_sentiment
                            WHERE symbol = $1 AND source = $2
                            AND date < CURRENT_DATE
                            ORDER BY date DESC LIMIT 1""",
                            symbol, fear_source,
                        )
                        if prev is not None and abs(fear_score - float(prev)) > 0.3:
                            notify_sentiment_shift(symbol, float(prev), fear_score)
            except Exception:
                pass  # / notification is best-effort

            logger.info("social_sentiment_processed", symbol=symbol, score=results[symbol])
        except Exception as exc:
            logger.warning("social_sentiment_failed", symbol=symbol, error=str(exc))
            results[symbol] = 0.0

    return results


from __future__ import annotations

import asyncio
import math
from datetime import date
from typing import Any

import asyncpg
import structlog

from src.notifications.notifier import notify_sentiment_shift

from .resilience import api_get, configure_rate_limit, with_retry
from .symbols import is_crypto

logger = structlog.get_logger(__name__)

APEWISDOM_BASE = "https://apewisdom.io/api/v1.0"
STOCKTWITS_BASE = "https://api.stocktwits.com/api/2"
FNG_URL = "https://api.alternative.me/fng/"

configure_rate_limit("apewisdom", max_concurrent=2, delay=1.0)
configure_rate_limit("stocktwits", max_concurrent=3, delay=1.0)
configure_rate_limit("fng", max_concurrent=2, delay=0.5)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

@with_retry(source="apewisdom", max_retries=2, base_delay=2.0)
async def fetch_apewisdom(filter_type: str = "all-stocks") -> dict[str, dict[str, Any]]:
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
            raw = math.log1p(mentions) / math.log1p(max_mentions) if max_mentions > 0 else 0.0
            result[ticker] = {
                "mentions": mentions,
                "upvotes": upvotes,
                "rank": rank,
                "raw_score": min(1.0, raw),
            }

        logger.debug("apewisdom_tickers", filter=filter_type, tickers=list(result.keys())[:10])
        logger.info("apewisdom_fetched", filter=filter_type, count=len(result))
    except Exception as exc:
        logger.warning("apewisdom_fetch_failed", filter=filter_type, error=str(exc)[:200])
    return result


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

@with_retry(source="stocktwits", max_retries=2, base_delay=2.0)
async def fetch_stocktwits_sentiment(symbol: str) -> dict[str, Any] | None:
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
# ---------------------------------------------------------------------------

@with_retry(source="fng", max_retries=2, base_delay=1.0)
async def fetch_fear_greed_index() -> dict[str, Any] | None:
    try:
        resp = await api_get(FNG_URL, source="fng")
        data = resp.json()
        logger.debug("fng_raw", data_len=len(data.get("data", [])),
                     first_entry=data.get("data", [{}])[0] if data.get("data") else None)

        value_str = data.get("data", [{}])[0].get("value")
        if value_str is None:
            logger.debug("fng_no_value")
            return None

        logger.debug("fng_parsed", value_str=value_str, type=type(value_str).__name__)
        value = float(value_str)
        if value < 0:
            logger.warning("fng_negative_sentinel", raw_value=value)
            return None
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

def _fetch_vix_sync() -> dict[str, float] | None:
    try:
        import yfinance as yf
        ticker = yf.Ticker("^VIX")
        hist = ticker.history(period="1d")
        if hist.empty:
            return None
        vix = float(hist["Close"].iloc[-1])
        normalized = max(-1.0, min(1.0, (30.0 - vix) / 20.0))
        return {"raw_level": vix, "normalized": normalized}
    except (KeyError, ValueError, IndexError, OSError, AttributeError):
        return None


async def fetch_vix() -> dict[str, float] | None:
    try:
        result = await asyncio.to_thread(_fetch_vix_sync)
        if result is not None:
            logger.info("vix_fetched", normalized=result["normalized"], raw=result["raw_level"])
        return result
    except Exception as exc:
        logger.debug("vix_fetch_failed", error=str(exc))
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


async def run_social_sentiment(
    pool: Any,
    symbols: list[str],
) -> dict[str, float]:
    results: dict[str, float] = {}

    fng_data = await fetch_fear_greed_index()  # / crypto fear & greed
    vix_score = await fetch_vix()              # / equity VIX-based fear gauge

    aw_stocks = await fetch_apewisdom("all-stocks")
    aw_crypto = await fetch_apewisdom("all-crypto")

    for symbol in symbols:
        try:
            if is_crypto(symbol):
                aw_ticker = symbol.replace("-USD", "")
                aw_data = aw_crypto.get(aw_ticker)
                if not aw_data:
                    logger.debug("apewisdom_crypto_miss", symbol=symbol, lookup=aw_ticker,
                                 available=list(aw_crypto.keys())[:15])
            else:
                aw_data = aw_stocks.get(symbol)

            if aw_data:
                await store_social_sentiment(
                    pool, symbol, "apewisdom",
                    None, None,
                    aw_data["mentions"],
                    aw_data["raw_score"],
                )

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
                        vix_score["normalized"],
                    )
                fear_score = vix_score["normalized"] if vix_score else None

            # / compute aggregate score
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
                    sum(s * w for s, w in zip(scores, weights, strict=False)) / total_weight
                ))
            else:
                results[symbol] = 0.0

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
            except (asyncpg.PostgresError, ValueError, TypeError):
                # / swallow notification best-effort
                pass

            logger.info("social_sentiment_processed", symbol=symbol, score=results[symbol])
        except Exception as exc:
            logger.warning("social_sentiment_failed", symbol=symbol, error=str(exc))
            results[symbol] = 0.0

    return results

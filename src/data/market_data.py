# / alpaca ohlcv via shared alpaca client + yfinance fallback for historical
# / graceful degradation: warns on failure, returns what it can

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog

from .alpaca_client import DATA_URL as ALPACA_DATA_URL, alpaca_headers, get_alpaca_client
from .resilience import with_retry
from .symbols import is_crypto, to_alpaca
from .validators import validate_ohlcv

logger = structlog.get_logger(__name__)

# / rate limit: 200 req/min for alpaca free tier
_rate_semaphore = asyncio.Semaphore(10)  # concurrency cap
_rate_delay = 0.3  # seconds between requests


def _alpaca_headers() -> dict[str, str]:
    return alpaca_headers()


@with_retry(source="alpaca_bars", max_retries=3, base_delay=1.0)
async def fetch_bars_alpaca(
    symbol: str,
    start: date,
    end: date,
    timeframe: str = "1Day",
) -> list[dict[str, Any]]:
    # / fetch ohlcv bars from alpaca rest api
    alpaca_sym = to_alpaca(symbol)
    crypto = is_crypto(symbol)

    if crypto:
        url = f"{ALPACA_DATA_URL}/v1beta3/crypto/us/bars"
        params = {
            "symbols": alpaca_sym,
            "timeframe": timeframe,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": 10000,
        }
    else:
        url = f"{ALPACA_DATA_URL}/v2/stocks/{alpaca_sym}/bars"
        params = {
            "timeframe": timeframe,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": 10000,
            "adjustment": "all",
        }

    all_bars: list[dict[str, Any]] = []

    async with _rate_semaphore:
        client = await get_alpaca_client()
        page_token = None
        while True:
            if page_token:
                params["page_token"] = page_token

            await asyncio.sleep(_rate_delay)
            resp = await client.get(url, headers=_alpaca_headers(), params=params, timeout=30.0)
            resp.raise_for_status()
            data = resp.json()

            if crypto:
                bars = data.get("bars", {}).get(alpaca_sym, [])
            else:
                bars = data.get("bars", [])

            for bar in bars:
                parsed = _parse_bar(symbol, bar)
                if parsed is not None:
                    all_bars.append(parsed)

            page_token = data.get("next_page_token")
            if not page_token:
                break

    logger.info("fetched_bars_alpaca", symbol=symbol, count=len(all_bars))
    return all_bars


async def fetch_bars_yfinance(
    symbol: str,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    # / fallback: yfinance for historical ohlcv (sync, run in thread)
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance_not_installed")
        return []

    yf_symbol = symbol

    def _fetch() -> list[dict[str, Any]]:
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(start=start.isoformat(), end=end.isoformat(), auto_adjust=True)
        if df is None or df.empty:
            return []

        bars = []
        for idx, row in df.iterrows():
            bar_date = idx.date() if hasattr(idx, "date") else idx
            import math
            if any(math.isnan(row.get(f, 0) or 0) for f in ("Open", "High", "Low", "Close")):
                continue
            bars.append({
                "symbol": symbol,
                "date": bar_date,
                "open": Decimal(str(round(row["Open"], 4))),
                "high": Decimal(str(round(row["High"], 4))),
                "low": Decimal(str(round(row["Low"], 4))),
                "close": Decimal(str(round(row["Close"], 4))),
                "volume": int(row.get("Volume", 0)),
                "vwap": None,
            })
        return bars

    try:
        bars = await asyncio.to_thread(_fetch)
        logger.info("fetched_bars_yfinance", symbol=symbol, count=len(bars))
        return bars
    except Exception as exc:
        logger.warning("yfinance_fetch_failed", symbol=symbol, error=str(exc))
        return []


async def fetch_bars(
    symbol: str,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    # / try alpaca first, fall back to yfinance
    try:
        bars = await fetch_bars_alpaca(symbol, start, end)
        if bars:
            return bars
        logger.warning("alpaca_returned_empty", symbol=symbol)
    except Exception as exc:
        logger.warning("alpaca_fetch_failed", symbol=symbol, error=str(exc))

    # / fallback
    logger.info("falling_back_to_yfinance", symbol=symbol)
    return await fetch_bars_yfinance(symbol, start, end)


@with_retry(source="alpaca_quote", max_retries=2, base_delay=0.5)
async def fetch_latest_quote(symbol: str) -> dict[str, Any] | None:
    # / get latest quote/trade for a symbol
    alpaca_sym = to_alpaca(symbol)
    crypto = is_crypto(symbol)

    if crypto:
        url = f"{ALPACA_DATA_URL}/v1beta3/crypto/us/latest/trades"
        params = {"symbols": alpaca_sym}
    else:
        url = f"{ALPACA_DATA_URL}/v2/stocks/{alpaca_sym}/trades/latest"
        params = {}

    client = await get_alpaca_client()
    resp = await client.get(url, headers=_alpaca_headers(), params=params, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()

    if crypto:
        trade = data.get("trades", {}).get(alpaca_sym)
        if trade:
            return {"symbol": symbol, "price": Decimal(str(trade["p"])), "timestamp": trade["t"]}
    else:
        trade = data.get("trade")
        if trade:
            return {"symbol": symbol, "price": Decimal(str(trade["p"])), "timestamp": trade["t"]}

    return None


async def store_bars(pool, bars: list[dict[str, Any]]) -> int:
    # / validate and insert bars, skip invalid, handle duplicates
    if not bars:
        return 0

    valid_bars = []
    for bar in bars:
        results = validate_ohlcv(bar)
        if all(r.valid for r in results):
            valid_bars.append(bar)
        else:
            invalid = [r for r in results if not r.valid]
            logger.warning(
                "bar_validation_failed",
                symbol=bar.get("symbol"),
                date=str(bar.get("date")),
                reasons=[r.reason for r in invalid],
            )

    if not valid_bars:
        return 0

    async with pool.acquire() as conn:
        inserted = 0
        for bar in valid_bars:
            try:
                await conn.execute(
                    """
                    INSERT INTO market_data (symbol, date, open, high, low, close, volume, vwap)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (symbol, date) DO UPDATE SET
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        volume = EXCLUDED.volume,
                        vwap = EXCLUDED.vwap
                    """,
                    bar["symbol"],
                    bar["date"],
                    bar["open"],
                    bar["high"],
                    bar["low"],
                    bar["close"],
                    bar["volume"],
                    bar.get("vwap"),
                )
                inserted += 1
            except Exception as exc:
                logger.warning(
                    "bar_insert_failed",
                    symbol=bar["symbol"],
                    date=str(bar["date"]),
                    error=str(exc),
                )

        logger.info("stored_bars", count=inserted, total=len(valid_bars))
        return inserted


async def store_intraday_bars(pool, bars: list[dict[str, Any]], timeframe: str = "2Hour") -> int:
    # / validate and insert intraday bars, handle duplicates via upsert
    if not bars:
        return 0

    async with pool.acquire() as conn:
        inserted = 0
        for bar in bars:
            try:
                await conn.execute(
                    """
                    INSERT INTO market_data_intraday (symbol, timestamp, timeframe, open, high, low, close, volume, vwap)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (symbol, timestamp, timeframe) DO UPDATE SET
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        volume = EXCLUDED.volume,
                        vwap = EXCLUDED.vwap
                    """,
                    bar["symbol"],
                    bar["timestamp"],
                    timeframe,
                    bar["open"],
                    bar["high"],
                    bar["low"],
                    bar["close"],
                    bar["volume"],
                    bar.get("vwap"),
                )
                inserted += 1
            except Exception as exc:
                logger.warning(
                    "intraday_bar_insert_failed",
                    symbol=bar["symbol"],
                    error=str(exc),
                )

        logger.info("stored_intraday_bars", count=inserted, timeframe=timeframe)
        return inserted


async def backfill_intraday(
    pool,
    symbols: list[str],
    days: int = 30,
    timeframe: str = "2Hour",
) -> dict[str, int]:
    # / backfill intraday bars, incremental from last stored timestamp
    # / end = tomorrow so alpaca returns today's intraday bars (end is exclusive)
    today = date.today()
    end = today + timedelta(days=1)
    start = today - timedelta(days=days)
    results: dict[str, int] = {}

    for symbol in symbols:
        try:
            # / check last stored timestamp for incremental fetch
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT MAX(timestamp) as max_ts FROM market_data_intraday
                    WHERE symbol = $1 AND timeframe = $2""",
                    symbol, timeframe,
                )
                if row and row["max_ts"]:
                    # / for intraday, re-fetch from last bar's date to get newer bars
                    fetch_start = row["max_ts"].date()
                else:
                    fetch_start = start

            bars = await fetch_bars_alpaca(symbol, fetch_start, end, timeframe=timeframe)
            count = await store_intraday_bars(pool, bars, timeframe=timeframe)
            results[symbol] = count
            logger.info("intraday_backfill_complete", symbol=symbol, bars=count)
        except Exception as exc:
            logger.warning("intraday_backfill_failed", symbol=symbol, error=str(exc))
            results[symbol] = 0

    return results


async def backfill(
    pool,
    symbols: list[str],
    years: int = 5,
) -> dict[str, int]:
    # / backfill historical data for all symbols, returns counts per symbol
    end = date.today()
    start = end - timedelta(days=years * 365)

    results: dict[str, int] = {}
    for symbol in symbols:
        try:
            # / check what we already have
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT MAX(date) as max_date FROM market_data WHERE symbol = $1",
                    symbol,
                )
                if row and row["max_date"]:
                    # / incremental: only fetch from last date + 1
                    existing_max = row["max_date"]
                    fetch_start = existing_max + timedelta(days=1)
                    if fetch_start >= end:
                        logger.info("backfill_up_to_date", symbol=symbol)
                        results[symbol] = 0
                        continue
                else:
                    fetch_start = start

            bars = await fetch_bars(symbol, fetch_start, end)
            count = await store_bars(pool, bars)
            results[symbol] = count
            logger.info("backfill_complete", symbol=symbol, bars=count)

        except Exception as exc:
            logger.warning("backfill_failed", symbol=symbol, error=str(exc))
            results[symbol] = 0
            # / graceful: continue with next symbol

    return results


def _parse_bar(symbol: str, bar: dict[str, Any]) -> dict[str, Any]:
    # / normalize alpaca bar response to our format
    timestamp = bar.get("t", "")
    if isinstance(timestamp, str) and "T" in timestamp:
        bar_dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        bar_date = bar_dt.date()
    else:
        logger.warning("unparseable_bar_timestamp", symbol=symbol, timestamp=timestamp)
        return None

    return {
        "symbol": symbol,
        "date": bar_date,
        "timestamp": bar_dt,  # / full datetime for intraday storage
        "open": Decimal(str(bar["o"])),
        "high": Decimal(str(bar["h"])),
        "low": Decimal(str(bar["l"])),
        "close": Decimal(str(bar["c"])),
        "volume": int(bar.get("v", 0)),
        "vwap": Decimal(str(bar["vw"])) if bar.get("vw") else None,
    }

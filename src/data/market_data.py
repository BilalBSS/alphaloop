# / alpaca ohlcv via shared alpaca client + yfinance fallback for historical
# / graceful degradation: warns on failure, returns what it can

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta, timezone
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


async def store_intraday_bars(pool, bars: list[dict[str, Any]], timeframe: str = "1Hour") -> int:
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


async def fetch_bars_yfinance_1h(symbol: str, start: date, end: date) -> list[dict[str, Any]]:
    # / yfinance 1h bars — free, 730 day lookback
    # / bug e2: yfinance returns multiindex columns like ('Open', 'AAPL') even for single
    # / ticker, so float(row["Open"]) raised valueerror and swallowed every equity intraday
    # / fetch. flatten columns to top level before iterating.
    def _fetch():
        import yfinance as yf
        earliest = date.today() - timedelta(days=729)
        fetch_start = max(start, earliest)
        df = yf.download(
            symbol, start=fetch_start.isoformat(), end=end.isoformat(),
            interval="1h", progress=False, auto_adjust=True,
        )
        if df is None or df.empty:
            return []
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
        bars = []
        for idx, row in df.iterrows():
            ts = idx.to_pydatetime()
            try:
                o = float(row["Open"]); h = float(row["High"])
                low = float(row["Low"]); c = float(row["Close"])
            except (ValueError, TypeError, KeyError):
                continue
            bars.append({
                "symbol": symbol,
                "date": ts.date(),
                "timestamp": ts,
                "open": Decimal(str(round(o, 4))),
                "high": Decimal(str(round(h, 4))),
                "low": Decimal(str(round(low, 4))),
                "close": Decimal(str(round(c, 4))),
                "volume": int(row.get("Volume", 0) or 0),
                "vwap": None,
            })
        return bars
    try:
        bars = await asyncio.to_thread(_fetch)
        logger.info("fetched_bars_yfinance_1h", symbol=symbol, count=len(bars))
        return bars
    except Exception as exc:
        logger.warning("yfinance_1h_fetch_failed", symbol=symbol, error=str(exc))
        return []


async def backfill_intraday(
    pool,
    symbols: list[str],
    days: int = 30,
    timeframe: str = "1Hour",
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

            try:
                bars = await fetch_bars_alpaca(symbol, fetch_start, end, timeframe=timeframe)
            except Exception:
                bars = []
            if not bars:
                # / fallback: yfinance 1h bars
                try:
                    bars = await fetch_bars_yfinance_1h(symbol, fetch_start, end)
                except Exception:
                    bars = []
            count = await store_intraday_bars(pool, bars, timeframe=timeframe)
            results[symbol] = count
            logger.info("intraday_backfill_complete", symbol=symbol, bars=count)
        except Exception as exc:
            logger.warning("intraday_backfill_failed", symbol=symbol, error=str(exc))
            results[symbol] = 0

    return results


async def aggregate_intraday_to_2h(
    pool,
    symbols: list[str],
    days: int = 10,
) -> dict[str, int]:
    # / build 2Hour bars from stored 1Hour bars via sql window aggregation
    # / bucket every pair of consecutive 1h bars into the lower one's even-hour timestamp
    # / idempotent upsert into market_data_intraday with timeframe='2Hour'
    if not symbols:
        return {}
    results: dict[str, int] = {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with pool.acquire() as conn:
        for symbol in symbols:
            try:
                rows = await conn.fetch(
                    """WITH src AS (
                        SELECT date_trunc('hour', timestamp)
                               - MAKE_INTERVAL(hours => MOD(EXTRACT(HOUR FROM timestamp)::int, 2)) AS bucket,
                               open, high, low, close, volume, vwap, timestamp
                        FROM market_data_intraday
                        WHERE symbol = $1 AND timeframe = '1Hour' AND timestamp >= $2
                    )
                    SELECT bucket AS timestamp,
                           (ARRAY_AGG(open ORDER BY timestamp ASC))[1] AS open,
                           MAX(high) AS high,
                           MIN(low) AS low,
                           (ARRAY_AGG(close ORDER BY timestamp DESC))[1] AS close,
                           SUM(volume) AS volume,
                           AVG(vwap) AS vwap
                    FROM src
                    GROUP BY bucket
                    HAVING COUNT(*) > 0
                    ORDER BY bucket ASC""",
                    symbol, cutoff,
                )
                if not rows:
                    results[symbol] = 0
                    continue
                inserted = 0
                for r in rows:
                    try:
                        await conn.execute(
                            """INSERT INTO market_data_intraday
                                (symbol, timestamp, timeframe, open, high, low, close, volume, vwap)
                            VALUES ($1, $2, '2Hour', $3, $4, $5, $6, $7, $8)
                            ON CONFLICT (symbol, timestamp, timeframe) DO UPDATE SET
                                open = EXCLUDED.open,
                                high = EXCLUDED.high,
                                low = EXCLUDED.low,
                                close = EXCLUDED.close,
                                volume = EXCLUDED.volume,
                                vwap = EXCLUDED.vwap""",
                            symbol, r["timestamp"],
                            r["open"], r["high"], r["low"], r["close"],
                            int(r["volume"] or 0), r["vwap"],
                        )
                        inserted += 1
                    except Exception as exc:
                        logger.warning("2h_bar_insert_failed", symbol=symbol, error=str(exc))
                results[symbol] = inserted
            except Exception as exc:
                logger.warning("2h_aggregate_failed", symbol=symbol, error=str(exc))
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


async def fetch_latest_prices(symbols: list[str]) -> dict[str, float]:
    # / batch fetch current prices via yfinance fast_info
    import asyncio
    prices: dict[str, float] = {}

    def _fetch_batch():
        import yfinance as yf
        result = {}
        for sym in symbols:
            try:
                yf_sym = sym.replace("-", "") if sym.endswith("-USD") else sym
                info = yf.Ticker(yf_sym).fast_info
                price = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
                if price and price > 0:
                    result[sym] = float(price)
            except Exception:
                pass
        return result

    try:
        prices = await asyncio.to_thread(_fetch_batch)
    except Exception:
        pass
    return prices


async def store_latest_prices(pool, prices: dict[str, float]) -> int:
    # / upsert into latest_prices (one row per symbol) — NOT market_data_intraday
    if not prices:
        return 0
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO latest_prices (symbol, price, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (symbol) DO UPDATE SET
                price = EXCLUDED.price,
                updated_at = NOW()
            """,
            [(sym, Decimal(str(price))) for sym, price in prices.items()],
        )
    return len(prices)


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

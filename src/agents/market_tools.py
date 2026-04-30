
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


async def fetch_daily_ohlcv(pool, symbol: str, limit: int = 250) -> list[dict]:
    # / recent daily bars, newest-first
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT date, open, high, low, close, volume
            FROM market_data WHERE symbol = $1
            ORDER BY date DESC LIMIT $2""",
            symbol, limit,
        )
    return [dict(r) for r in rows]


async def fetch_intraday_ohlcv(
    pool, symbol: str, timeframe: str = "1Hour", limit: int = 100,
) -> list[dict]:
    # / recent intraday bars
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT timestamp, open, high, low, close, volume
            FROM market_data_intraday WHERE symbol = $1 AND timeframe = $2
            ORDER BY timestamp DESC LIMIT $3""",
            symbol, timeframe, limit,
        )
    return [dict(r) for r in rows]


async def fetch_close_history_batch(
    pool, symbols: list[str], bars_per_symbol: int = 252,
) -> list[dict]:
    if not symbols:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT symbol, date, close FROM market_data
            WHERE symbol = ANY($1)
            ORDER BY date DESC LIMIT $2""",
            symbols, bars_per_symbol * len(symbols),
        )
    return [dict(r) for r in rows]


async def fetch_latest_regime(pool, market: str = "equity") -> str | None:
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT regime, date FROM regime_history
                WHERE market = $1 ORDER BY date DESC LIMIT 1""",
                market,
            )
            if not row:
                logger.warning("regime_missing", market=market)
                return None
            from datetime import date as _date
            regime_date = row.get("date") if hasattr(row, "get") else None
            if regime_date and (_date.today() - regime_date).days > 2:
                logger.warning("regime_stale", market=market, last_date=str(regime_date))
            return row["regime"]
    except (asyncpg.PostgresError, KeyError, TypeError):
        return None


async def fetch_recent_pnl(pool, limit: int = 5) -> list[float]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT pnl FROM trade_log
            WHERE pnl IS NOT NULL
            ORDER BY created_at DESC LIMIT $1""", limit)
    return [float(r["pnl"]) for r in rows]


async def fetch_avg_volume(pool, symbol: str, days: int = 20) -> float | None:
    # / rolling average daily volume
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT AVG(volume) as avg_vol FROM (
                SELECT volume FROM market_data
                WHERE symbol = $1 AND volume > 0
                ORDER BY date DESC LIMIT $2
            ) sub""", symbol, days)
    return float(row["avg_vol"]) if row and row["avg_vol"] else None


async def fetch_symbol_beta(
    pool, symbol: str, benchmark: str = "SPY", window: int = 60,
) -> float | None:
    # / rolling beta vs benchmark
    import numpy as np
    async with pool.acquire() as conn:
        sym_rows = await conn.fetch(
            "SELECT date, close FROM market_data WHERE symbol = $1 ORDER BY date DESC LIMIT $2",
            symbol, window + 1)
        bench_rows = await conn.fetch(
            "SELECT date, close FROM market_data WHERE symbol = $1 ORDER BY date DESC LIMIT $2",
            benchmark, window + 1)
    if len(sym_rows) < 20 or len(bench_rows) < 20:
        return None
    sym_map = {r["date"]: float(r["close"]) for r in sym_rows}
    bench_map = {r["date"]: float(r["close"]) for r in bench_rows}
    common = sorted(set(sym_map) & set(bench_map))
    if len(common) < 20:
        return None
    sym_prices = [sym_map[d] for d in common]
    bench_prices = [bench_map[d] for d in common]
    sym_ret = np.diff(sym_prices) / np.array(sym_prices[:-1])
    bench_ret = np.diff(bench_prices) / np.array(bench_prices[:-1])
    cov = np.cov(sym_ret, bench_ret)
    if cov[1, 1] == 0:
        return None
    return float(cov[0, 1] / cov[1, 1])


async def fetch_peak_equity(pool) -> float:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT MAX(peak_equity) FROM portfolio_snapshots")
        return float(row[0]) if row and row[0] else 0.0


async def store_peak_equity(pool, equity: float, peak: float) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO portfolio_snapshots (date, equity, peak_equity)
            VALUES ($1, $2, $3)
            ON CONFLICT (date) DO UPDATE SET equity = EXCLUDED.equity,
                peak_equity = GREATEST(portfolio_snapshots.peak_equity, EXCLUDED.peak_equity)""",
            date.today(), Decimal(str(equity)), Decimal(str(peak)),
        )


async def store_computed_indicators(
    pool, symbol: str, indicators: dict[str, Any], timeframe: str = "1Day",
) -> None:
    # / dashboard latest indicator values
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO computed_indicators
                (symbol, date, timeframe, rsi14, macd, macd_signal, macd_histogram,
                 adx, sma20, sma50, bb_upper, bb_middle, bb_lower, atr, hurst)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                ON CONFLICT (symbol, date, timeframe) DO UPDATE SET
                    rsi14 = EXCLUDED.rsi14, macd = EXCLUDED.macd,
                    macd_signal = EXCLUDED.macd_signal, macd_histogram = EXCLUDED.macd_histogram,
                    adx = EXCLUDED.adx, sma20 = EXCLUDED.sma20, sma50 = EXCLUDED.sma50,
                    bb_upper = EXCLUDED.bb_upper, bb_middle = EXCLUDED.bb_middle,
                    bb_lower = EXCLUDED.bb_lower, atr = EXCLUDED.atr,
                    hurst = EXCLUDED.hurst,
                    created_at = NOW()""",
                symbol, date.today(), timeframe,
                indicators.get("rsi14"), indicators.get("macd"),
                indicators.get("macd_signal"), indicators.get("macd_histogram"),
                indicators.get("adx"), indicators.get("sma20"), indicators.get("sma50"),
                indicators.get("bb_upper"), indicators.get("bb_middle"),
                indicators.get("bb_lower"), indicators.get("atr"),
                indicators.get("hurst"),
            )
    except Exception as exc:
        logger.warning("store_indicators_failed", symbol=symbol, error=str(exc))


async def store_ict_indicators(
    pool, symbol: str, ict_data: dict[str, Any],
) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO computed_indicators (symbol, date, timeframe, ict_data)
                VALUES ($1, $2, '1Day', $3)
                ON CONFLICT (symbol, date, timeframe) DO UPDATE SET ict_data = EXCLUDED.ict_data, created_at = NOW()""",
                symbol, date.today(), json.dumps(ict_data),
            )
    except Exception as exc:
        logger.warning("store_ict_failed", symbol=symbol, error=str(exc))

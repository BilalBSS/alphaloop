
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import asyncpg
import numpy as np
import structlog

from src.data.symbols import (
    SECTORS,
    get_sector,
    is_crypto,
)

logger = structlog.get_logger(__name__)

MIN_HISTORY_DAYS = 200

EQUITY_HIGH_VOL_MULTIPLIER = 2.0  # / vol > 2x median
CRYPTO_HIGH_VOL_MULTIPLIER = 2.0  # / same multiplier but crypto
DRAWDOWN_BEAR_THRESHOLD = 0.15  # / > 15% drawdown from
DRAWDOWN_BULL_MAX = 0.10  # / < 10% drawdown for
VOL_BULL_MULTIPLIER = 1.5  # / vol < 1.5x median

SECTOR_REGIME_CACHE_TTL_SECONDS = 21600
SECTOR_MIN_SYMBOLS = 3

_sector_regime_cache: dict[str, tuple[str, float, float]] = {}


@dataclass
class RegimeResult:
    date: date
    market: str          # "equity" or "crypto"
    regime: str  # / bull, bear, sideways, high_vol,
    confidence: float    # 0.0 to 1.0
    volatility_20d: float
    sma50_above_200: bool | None
    drawdown_from_high: float


def classify_regimes(
    dates: list[date],
    closes: list[float],
    market: str = "equity",
) -> list[RegimeResult]:
    if len(dates) != len(closes):
        raise ValueError("dates and closes must be same length")

    if len(dates) == 0:
        return []

    closes_arr = np.array(closes, dtype=np.float64)
    results: list[RegimeResult] = []

    for i in range(len(dates)):
        if i < MIN_HISTORY_DAYS:
            results.append(RegimeResult(
                date=dates[i],
                market=market,
                regime="insufficient_data",
                confidence=0.0,
                volatility_20d=0.0,
                sma50_above_200=None,
                drawdown_from_high=0.0,
            ))
            continue

        window = closes_arr[:i + 1]
        vol_20d = _rolling_volatility(window, 20)
        sma50 = _sma(window, 50)
        sma200 = _sma(window, 200)
        drawdown = _drawdown_from_high(window, 252)
        vol_median = _median_volatility(window, 252, 20)

        sma50_above = sma50 > sma200 if sma50 is not None and sma200 is not None else None

        regime, confidence = _classify_single(
            vol_20d=vol_20d,
            vol_median=vol_median,
            sma50_above_200=sma50_above,
            drawdown=drawdown,
            high_vol_mult=CRYPTO_HIGH_VOL_MULTIPLIER if market == "crypto" else EQUITY_HIGH_VOL_MULTIPLIER,
        )

        results.append(RegimeResult(
            date=dates[i],
            market=market,
            regime=regime,
            confidence=confidence,
            volatility_20d=vol_20d,
            sma50_above_200=sma50_above,
            drawdown_from_high=drawdown,
        ))

    return results


def classify_single_date(
    closes: list[float],
    as_of: date,
    market: str = "equity",
) -> RegimeResult:
    if len(closes) < MIN_HISTORY_DAYS:
        return RegimeResult(
            date=as_of,
            market=market,
            regime="insufficient_data",
            confidence=0.0,
            volatility_20d=0.0,
            sma50_above_200=None,
            drawdown_from_high=0.0,
        )

    window = np.array(closes, dtype=np.float64)
    vol_20d = _rolling_volatility(window, 20)
    sma50 = _sma(window, 50)
    sma200 = _sma(window, 200)
    drawdown = _drawdown_from_high(window, 252)
    vol_median = _median_volatility(window, 252, 20)

    sma50_above = sma50 > sma200 if sma50 is not None and sma200 is not None else None

    regime, confidence = _classify_single(
        vol_20d=vol_20d,
        vol_median=vol_median,
        sma50_above_200=sma50_above,
        drawdown=drawdown,
        high_vol_mult=CRYPTO_HIGH_VOL_MULTIPLIER if market == "crypto" else EQUITY_HIGH_VOL_MULTIPLIER,
    )

    return RegimeResult(
        date=as_of,
        market=market,
        regime=regime,
        confidence=confidence,
        volatility_20d=vol_20d,
        sma50_above_200=sma50_above,
        drawdown_from_high=drawdown,
    )


def _classify_single(
    vol_20d: float,
    vol_median: float,
    sma50_above_200: bool | None,
    drawdown: float,
    high_vol_mult: float,
) -> tuple[str, float]:

    signals = {
        "vol_elevated": vol_20d > high_vol_mult * vol_median if vol_median > 0 else False,
        "vol_low": vol_20d < VOL_BULL_MULTIPLIER * vol_median if vol_median > 0 else True,
        "trend_up": sma50_above_200 is True,
        "trend_down": sma50_above_200 is False,
        "deep_drawdown": drawdown > DRAWDOWN_BEAR_THRESHOLD,
        "shallow_drawdown": drawdown < DRAWDOWN_BULL_MAX,
    }

    if signals["vol_elevated"]:
        supporting = sum([
            signals["vol_elevated"],
            signals["deep_drawdown"],
            signals["trend_down"],
        ])
        return "high_vol", round(supporting / 3, 2)

    if signals["trend_down"] and signals["deep_drawdown"]:
        supporting = sum([
            signals["trend_down"],
            signals["deep_drawdown"],
            signals["vol_elevated"],
        ])
        return "bear", round(supporting / 3, 2)

    if signals["trend_up"] and signals["shallow_drawdown"]:
        supporting = sum([
            signals["trend_up"],
            signals["shallow_drawdown"],
            signals["vol_low"],
        ])
        return "bull", round(supporting / 3, 2)

    # / sideways: default
    supporting = sum([
        not signals["trend_up"] and not signals["trend_down"],
        not signals["deep_drawdown"] and not signals["shallow_drawdown"],
        not signals["vol_elevated"],
    ])
    return "sideways", round(max(supporting / 3, 0.33), 2)


def _rolling_volatility(prices: np.ndarray, window: int) -> float:
    if len(prices) < window + 1:
        return 0.0
    log_returns = np.diff(np.log(prices[-window - 1:]))
    return float(np.std(log_returns) * np.sqrt(252))


def _sma(prices: np.ndarray, window: int) -> float | None:
    # / simple moving average
    if len(prices) < window:
        return None
    return float(np.mean(prices[-window:]))


def _drawdown_from_high(prices: np.ndarray, lookback: int) -> float:
    window = prices[-lookback:] if len(prices) >= lookback else prices
    if len(window) == 0:
        return 0.0
    peak = float(np.max(window))
    current = float(prices[-1])
    if peak <= 0:
        return 0.0
    return (peak - current) / peak


def _median_volatility(prices: np.ndarray, lookback: int, vol_window: int) -> float:
    if len(prices) < lookback + vol_window:
        # / use what we have
        lookback = max(len(prices) - vol_window, vol_window)

    vols = []
    start = max(0, len(prices) - lookback)
    for i in range(start + vol_window, len(prices)):
        v = _rolling_volatility(prices[:i + 1], vol_window)
        if v > 0:
            vols.append(v)

    return float(np.median(vols)) if vols else 0.0


async def backfill_regimes(
    pool: asyncpg.Pool,
    index_symbol: str = "SPY",
    market: str = "equity",
) -> int:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT date, close FROM market_data
            WHERE symbol = $1 ORDER BY date ASC
            """,
            index_symbol,
        )

    if not rows:
        logger.warning("no_index_data_for_regime", symbol=index_symbol)
        return 0

    dates = [r["date"] for r in rows]
    closes = [float(r["close"]) for r in rows]

    results = classify_regimes(dates, closes, market)
    classified = [r for r in results if r.regime != "insufficient_data"]

    if not classified:
        logger.warning("all_insufficient_data", symbol=index_symbol)
        return 0

    # / store to regime_history
    async with pool.acquire() as conn:
        for r in classified:
            await conn.execute(
                """
                INSERT INTO regime_history (date, market, regime, confidence,
                    volatility_20d, trend_sma50_above_200, drawdown_from_high)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT (date, market) DO UPDATE SET
                    regime = EXCLUDED.regime,
                    confidence = EXCLUDED.confidence,
                    volatility_20d = EXCLUDED.volatility_20d,
                    trend_sma50_above_200 = EXCLUDED.trend_sma50_above_200,
                    drawdown_from_high = EXCLUDED.drawdown_from_high
                """,
                r.date, r.market, r.regime, Decimal(str(r.confidence)),
                Decimal(str(round(r.volatility_20d, 4))),
                r.sma50_above_200,
                Decimal(str(round(r.drawdown_from_high, 4))),
            )

    async with pool.acquire() as conn:
        if market == "crypto":
            await conn.execute(
                """
                UPDATE market_data md
                SET regime = rh.regime,
                    regime_confidence = rh.confidence
                FROM regime_history rh
                WHERE md.date = rh.date
                  AND rh.market = $1
                  AND md.symbol LIKE '%-%'
                  AND md.regime IS DISTINCT FROM rh.regime
                """,
                market,
            )
        else:
            await conn.execute(
                """
                UPDATE market_data md
                SET regime = rh.regime,
                    regime_confidence = rh.confidence
                FROM regime_history rh
                WHERE md.date = rh.date
                  AND rh.market = $1
                  AND md.symbol NOT LIKE '%-%'
                  AND md.regime IS DISTINCT FROM rh.regime
                """,
                market,
            )

    count = len(classified)
    logger.info("backfilled_regimes", market=market, count=count)
    return count


def _sector_market_type(sector: str) -> str:
    symbols = SECTORS.get(sector, [])
    if not symbols:
        return "equity"
    return "crypto" if is_crypto(symbols[0]) else "equity"


async def _fetch_sector_composite_closes(
    pool: asyncpg.Pool, sector: str, lookback_days: int = 400,
) -> tuple[list[date], list[float]]:
    symbols = SECTORS.get(sector, [])
    if len(symbols) < 2:
        return [], []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT date, symbol, close
            FROM market_data
            WHERE symbol = ANY($1::varchar[])
              AND close IS NOT NULL
            ORDER BY date ASC, symbol ASC
            """,
            symbols,
        )

    if not rows:
        return [], []

    by_date: dict[date, dict[str, float]] = {}
    for r in rows:
        by_date.setdefault(r["date"], {})[r["symbol"]] = float(r["close"])

    first_close: dict[str, float] = {}
    for d in sorted(by_date.keys()):
        for sym, px in by_date[d].items():
            if sym not in first_close and px > 0:
                first_close[sym] = px

    dates: list[date] = []
    composites: list[float] = []
    for d in sorted(by_date.keys()):
        rebased = []
        for sym, px in by_date[d].items():
            if sym in first_close and first_close[sym] > 0:
                rebased.append(px / first_close[sym])
        if len(rebased) >= 2:
            dates.append(d)
            composites.append(float(np.mean(rebased)) * 100.0)  # / index level base=100

    if len(dates) > lookback_days:
        dates = dates[-lookback_days:]
        composites = composites[-lookback_days:]

    return dates, composites


async def detect_market_regime(
    pool: asyncpg.Pool, market: str = "equity",
) -> tuple[str, float]:
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT regime, confidence FROM regime_history
                WHERE market = $1 ORDER BY date DESC LIMIT 1""",
                market,
            )
        if not row:
            return ("insufficient_data", 0.0)
        regime = row["regime"]
        conf = float(row["confidence"]) if row["confidence"] is not None else 0.0
        return (regime, conf)
    except Exception as exc:
        logger.warning("detect_market_regime_failed", market=market, error=str(exc)[:200])
        return ("insufficient_data", 0.0)


async def detect_regime_per_sector(
    pool: asyncpg.Pool, force_refresh: bool = False,
) -> dict[str, tuple[str, float]]:
    # / returns {sector_name: (regime, confidence)}
    now = time.time()

    if not force_refresh and _sector_regime_cache:
        first_ts = next(iter(_sector_regime_cache.values()))[2]
        if now - first_ts < SECTOR_REGIME_CACHE_TTL_SECONDS:
            return {k: (v[0], v[1]) for k, v in _sector_regime_cache.items()}

    equity_fallback = await detect_market_regime(pool, "equity")
    crypto_fallback = await detect_market_regime(pool, "crypto")

    out: dict[str, tuple[str, float]] = {}
    for sector in SECTORS:
        market = _sector_market_type(sector)
        fallback = crypto_fallback if market == "crypto" else equity_fallback

        symbols = SECTORS[sector]
        if len(symbols) < SECTOR_MIN_SYMBOLS:
            out[sector] = fallback
            continue

        try:
            dates, closes = await _fetch_sector_composite_closes(pool, sector)
        except Exception as exc:
            logger.warning("sector_composite_fetch_failed", sector=sector, error=str(exc)[:200])
            out[sector] = fallback
            continue

        if len(closes) < MIN_HISTORY_DAYS:
            logger.debug(
                "sector_insufficient_history", sector=sector,
                have=len(closes), need=MIN_HISTORY_DAYS,
            )
            out[sector] = fallback
            continue

        try:
            result = classify_single_date(closes, dates[-1], market)
        except Exception as exc:
            logger.warning("sector_classify_failed", sector=sector, error=str(exc)[:200])
            out[sector] = fallback
            continue

        if result.regime == "insufficient_data":
            out[sector] = fallback
        else:
            out[sector] = (result.regime, result.confidence)

    ts = time.time()
    _sector_regime_cache.clear()
    for sector, (regime, conf) in out.items():
        _sector_regime_cache[sector] = (regime, conf, ts)

    logger.info(
        "per_sector_regime_computed",
        sectors={s: r for s, (r, _) in out.items()},
    )
    return out


async def detect_regime_for_symbol(
    symbol: str, pool: asyncpg.Pool,
) -> tuple[str, float]:
    sector = get_sector(symbol)
    market = "crypto" if is_crypto(symbol) else "equity"

    if sector is None:
        return await detect_market_regime(pool, market)

    per_sector = await detect_regime_per_sector(pool)
    if sector in per_sector:
        return per_sector[sector]

    return await detect_market_regime(pool, market)


async def backfill_regimes_per_sector(pool: asyncpg.Pool) -> dict[str, int]:
    per_sector = await detect_regime_per_sector(pool, force_refresh=True)

    today = date.today()
    written: dict[str, int] = {}

    async with pool.acquire() as conn:
        for sector, (regime, confidence) in per_sector.items():
            if regime == "insufficient_data":
                written[sector] = 0
                continue
            try:
                await conn.execute(
                    """
                    INSERT INTO regime_history (date, market, regime, confidence)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (date, market) DO UPDATE SET
                        regime = EXCLUDED.regime,
                        confidence = EXCLUDED.confidence
                    """,
                    today, sector, regime,
                    Decimal(str(round(confidence, 3))),
                )
                written[sector] = 1
            except Exception as exc:
                logger.warning(
                    "sector_regime_write_failed",
                    sector=sector, error=str(exc)[:200],
                )
                written[sector] = 0

    total = sum(written.values())
    logger.info("backfilled_sector_regimes", total=total, per_sector=written)
    return written


def _clear_sector_regime_cache() -> None:
    _sector_regime_cache.clear()


async def snapshot_regime_daily(
    pool: asyncpg.Pool, market: str, current_regime: str | None,
    confidence: float | None = None,
) -> bool:
    if not market or not current_regime or current_regime == "insufficient_data":
        return False

    today = date.today()
    try:
        async with pool.acquire() as conn:
            existing = await conn.fetchval(
                """
                SELECT 1 FROM regime_history
                WHERE market = $1 AND date = $2
                LIMIT 1
                """,
                market, today,
            )
            if existing:
                return False

            await conn.execute(
                """
                INSERT INTO regime_history (date, market, regime, confidence, is_snapshot)
                VALUES ($1, $2, $3, $4, TRUE)
                ON CONFLICT (date, market) DO NOTHING
                """,
                today, market, current_regime,
                Decimal(str(round(confidence, 3))) if confidence is not None else None,
            )
    except Exception as exc:
        logger.warning(
            "regime_snapshot_write_failed",
            market=market, regime=current_regime, error=str(exc)[:200],
        )
        return False

    logger.info("regime_snapshot_written", market=market, regime=current_regime)
    return True

# / market regime classifier: bull, bear, sideways, high_vol, insufficient_data
# / rule-based using rolling volatility, sma cross, drawdown
# / separate classifiers for equity (spy-based) and crypto (btc-based)
# / graceful: returns insufficient_data when not enough history

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import numpy as np
import structlog

from .symbols import market_type

logger = structlog.get_logger(__name__)

# / minimum trading days needed for sma200
MIN_HISTORY_DAYS = 200

# / volatility thresholds (equity vs crypto)
EQUITY_HIGH_VOL_MULTIPLIER = 2.0   # vol > 2x median = high_vol
CRYPTO_HIGH_VOL_MULTIPLIER = 2.0   # same multiplier but crypto baseline is ~3x equity
DRAWDOWN_BEAR_THRESHOLD = 0.15     # > 15% drawdown from high
DRAWDOWN_BULL_MAX = 0.10           # < 10% drawdown for bull
VOL_BULL_MULTIPLIER = 1.5          # vol < 1.5x median for bull


@dataclass
class RegimeResult:
    date: date
    market: str          # "equity" or "crypto"
    regime: str          # bull, bear, sideways, high_vol, insufficient_data
    confidence: float    # 0.0 to 1.0
    volatility_20d: float
    sma50_above_200: bool | None
    drawdown_from_high: float


def classify_regimes(
    dates: list[date],
    closes: list[float],
    market: str = "equity",
) -> list[RegimeResult]:
    # / classify regime for each date given close prices
    # / returns one result per date (early dates get insufficient_data)
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

        # / compute indicators using data up to this point
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
    # / classify regime for the most recent date given price history
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
    # / priority: high_vol > bear > bull > sideways
    # / confidence = (supporting signals) / 3

    signals = {
        "vol_elevated": vol_20d > high_vol_mult * vol_median if vol_median > 0 else False,
        "vol_low": vol_20d < VOL_BULL_MULTIPLIER * vol_median if vol_median > 0 else True,
        "trend_up": sma50_above_200 is True,
        "trend_down": sma50_above_200 is False,
        "deep_drawdown": drawdown > DRAWDOWN_BEAR_THRESHOLD,
        "shallow_drawdown": drawdown < DRAWDOWN_BULL_MAX,
    }

    # / high_vol: vol > 2x median (overrides everything)
    if signals["vol_elevated"]:
        supporting = sum([
            signals["vol_elevated"],
            signals["deep_drawdown"],
            signals["trend_down"],
        ])
        return "high_vol", round(supporting / 3, 2)

    # / bear: trend down + deep drawdown
    if signals["trend_down"] and signals["deep_drawdown"]:
        supporting = sum([
            signals["trend_down"],
            signals["deep_drawdown"],
            signals["vol_elevated"],
        ])
        return "bear", round(supporting / 3, 2)

    # / bull: trend up + shallow drawdown + low vol
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
    # / annualized volatility from log returns over window
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
    # / current drawdown from highest point in lookback period
    window = prices[-lookback:] if len(prices) >= lookback else prices
    if len(window) == 0:
        return 0.0
    peak = float(np.max(window))
    current = float(prices[-1])
    if peak <= 0:
        return 0.0
    return (peak - current) / peak


def _median_volatility(prices: np.ndarray, lookback: int, vol_window: int) -> float:
    # / median of rolling volatility over lookback period
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
    pool,
    index_symbol: str = "SPY",
    market: str = "equity",
) -> int:
    # / compute regimes from market_data and populate regime_history + update market_data rows
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

    # / update market_data rows with regime tags (only matching market type)
    async with pool.acquire() as conn:
        if market == "crypto":
            # / crypto symbols contain '-' or '/' (e.g. BTC-USD)
            updated = await conn.execute(
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
            # / equity symbols are plain tickers (no '-')
            updated = await conn.execute(
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

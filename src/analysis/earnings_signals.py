# / earnings signals: surprise detection, guidance tracking, estimate revisions
# / uses yfinance earnings data (free)
# / scores bullish/bearish signals based on earnings patterns

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# / signal thresholds
SURPRISE_THRESHOLD = 0.05  # 5% beat/miss is significant
CONSECUTIVE_BEATS_BULLISH = 3  # 3+ consecutive beats = strong signal
REVISION_THRESHOLD = 0.03  # 3% revision is meaningful


@dataclass
class EarningsSignal:
    symbol: str
    date: date
    signal: str           # bullish, bearish, neutral
    strength: float       # 0-100
    surprise_pct: float | None       # latest quarter surprise %
    consecutive_beats: int           # streak of beats (negative = misses)
    avg_surprise_4q: float | None    # average surprise over 4 quarters
    details: dict[str, Any] = field(default_factory=dict)


def _fetch_earnings_sync(symbol: str) -> dict[str, Any] | None:
    # / sync yfinance earnings fetch
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance_not_installed")
        return None

    try:
        ticker = yf.Ticker(symbol)

        # / get quarterly earnings
        earnings = getattr(ticker, "quarterly_earnings", None)
        if earnings is None or (hasattr(earnings, "empty") and earnings.empty):
            # / try earnings_history as fallback
            earnings = getattr(ticker, "earnings_history", None)

        # / get earnings dates for forward-looking
        earnings_dates = getattr(ticker, "earnings_dates", None)

        result: dict[str, Any] = {"symbol": symbol, "quarters": []}

        # / parse quarterly earnings if available
        if earnings is not None and hasattr(earnings, "iterrows"):
            for idx, row in earnings.iterrows():
                q: dict[str, Any] = {"period": str(idx)}

                # / different yfinance versions use different column names
                for actual_col in ("Actual", "actual", "Reported EPS", "reportedEPS"):
                    if actual_col in row.index:
                        q["actual"] = float(row[actual_col]) if row[actual_col] is not None else None
                        break

                for est_col in ("Estimate", "estimate", "Estimated EPS", "estimatedEPS"):
                    if est_col in row.index:
                        q["estimate"] = float(row[est_col]) if row[est_col] is not None else None
                        break

                # / compute surprise
                if q.get("actual") is not None and q.get("estimate") is not None and q["estimate"] != 0:
                    q["surprise_pct"] = (q["actual"] - q["estimate"]) / abs(q["estimate"])
                else:
                    q["surprise_pct"] = None

                result["quarters"].append(q)

        # / parse earnings dates for next report
        if earnings_dates is not None and hasattr(earnings_dates, "index") and len(earnings_dates) > 0:
            try:
                next_date = earnings_dates.index[0]
                if hasattr(next_date, "date"):
                    next_date = next_date.date()
                result["next_earnings_date"] = str(next_date)
            except Exception:
                pass

        return result

    except Exception as exc:
        logger.warning("earnings_fetch_error", symbol=symbol, error=str(exc))
        return None


async def fetch_earnings(symbol: str) -> dict[str, Any] | None:
    # / async wrapper
    try:
        return await asyncio.to_thread(_fetch_earnings_sync, symbol)
    except Exception as exc:
        logger.warning("earnings_fetch_failed", symbol=symbol, error=str(exc))
        return None


def compute_earnings_signal(earnings_data: dict[str, Any]) -> EarningsSignal:
    # / analyze earnings history and produce a signal
    symbol = earnings_data.get("symbol", "UNKNOWN")
    quarters = earnings_data.get("quarters", [])

    if not quarters:
        return EarningsSignal(
            symbol=symbol,
            date=date.today(),
            signal="neutral",
            strength=0.0,
            surprise_pct=None,
            consecutive_beats=0,
            avg_surprise_4q=None,
            details={"reason": "no_earnings_data"},
        )

    # / get surprises (most recent first)
    surprises = [q["surprise_pct"] for q in quarters if q.get("surprise_pct") is not None]

    if not surprises:
        return EarningsSignal(
            symbol=symbol,
            date=date.today(),
            signal="neutral",
            strength=0.0,
            surprise_pct=None,
            consecutive_beats=0,
            avg_surprise_4q=None,
            details={"reason": "no_surprise_data"},
        )

    latest_surprise = surprises[0]

    # / count consecutive beats/misses from most recent
    consecutive = 0
    for s in surprises:
        if s > SURPRISE_THRESHOLD:
            if consecutive >= 0:
                consecutive += 1
            else:
                break
        elif s < -SURPRISE_THRESHOLD:
            if consecutive <= 0:
                consecutive -= 1
            else:
                break
        else:
            break

    # / average surprise over last 4 quarters
    recent_4 = surprises[:4]
    avg_surprise = sum(recent_4) / len(recent_4)

    # / compute signal and strength
    strength = 0.0
    signal = "neutral"

    # / latest surprise contribution (0-40 points)
    if abs(latest_surprise) > SURPRISE_THRESHOLD:
        surprise_points = min(40.0, abs(latest_surprise) / 0.20 * 40)
        strength += surprise_points
        signal = "bullish" if latest_surprise > 0 else "bearish"

    # / consecutive beats/misses contribution (0-30 points)
    if abs(consecutive) >= CONSECUTIVE_BEATS_BULLISH:
        strength += 30.0
    elif abs(consecutive) >= 2:
        strength += 15.0

    # / average surprise contribution (0-30 points)
    if abs(avg_surprise) > SURPRISE_THRESHOLD:
        avg_points = min(30.0, abs(avg_surprise) / 0.15 * 30)
        strength += avg_points

    # / direction alignment
    if avg_surprise > SURPRISE_THRESHOLD and latest_surprise > SURPRISE_THRESHOLD:
        signal = "bullish"
    elif avg_surprise < -SURPRISE_THRESHOLD and latest_surprise < -SURPRISE_THRESHOLD:
        signal = "bearish"
    elif strength < 20:
        signal = "neutral"

    strength = min(100.0, round(strength, 1))

    return EarningsSignal(
        symbol=symbol,
        date=date.today(),
        signal=signal,
        strength=strength,
        surprise_pct=round(latest_surprise, 4),
        consecutive_beats=consecutive,
        avg_surprise_4q=round(avg_surprise, 4),
        details={
            "quarters_analyzed": len(surprises),
            "next_earnings": earnings_data.get("next_earnings_date"),
        },
    )


async def analyze_earnings(symbol: str) -> EarningsSignal | None:
    # / full pipeline: fetch + compute signal
    data = await fetch_earnings(symbol)
    if not data:
        return None

    signal = compute_earnings_signal(data)
    logger.info(
        "earnings_signal_complete",
        symbol=symbol,
        signal=signal.signal,
        strength=signal.strength,
    )
    return signal

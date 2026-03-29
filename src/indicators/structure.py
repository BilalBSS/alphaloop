# / ict/smart money indicators: fair value gaps, order blocks, structure breaks
# / all take pandas series, return dataclasses
# / nan-safe: insufficient data returns empty results

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import structlog

from .volatility import atr as compute_atr

logger = structlog.get_logger(__name__)


@dataclass
class FairValueGap:
    index: int
    type: str       # "bullish" or "bearish"
    high: float     # upper bound of gap
    low: float      # lower bound of gap
    filled: bool


@dataclass
class FairValueGapResult:
    gaps: list[FairValueGap]
    # / per-bar series: 1 = bullish fvg at bar, -1 = bearish, 0 = none
    signal: pd.Series


def fair_value_gaps(
    high: pd.Series, low: pd.Series, close: pd.Series,
) -> FairValueGapResult:
    # / 3-candle pattern: gap between candle 1 and candle 3
    n = len(close)
    signal = pd.Series(0, index=close.index, dtype=int)
    gaps: list[FairValueGap] = []

    if n < 3:
        return FairValueGapResult(gaps=[], signal=signal)

    for i in range(2, n):
        # / bullish fvg: candle 3 low > candle 1 high
        if low.iloc[i] > high.iloc[i - 2]:
            gap_high = low.iloc[i]
            gap_low = high.iloc[i - 2]
            # / check if filled by any subsequent bar
            filled = False
            for j in range(i + 1, n):
                if low.iloc[j] <= gap_high:
                    filled = True
                    break
            gaps.append(FairValueGap(
                index=i - 1, type="bullish",
                high=gap_high, low=gap_low, filled=filled,
            ))
            signal.iloc[i - 1] = 1

        # / bearish fvg: candle 3 high < candle 1 low
        elif high.iloc[i] < low.iloc[i - 2]:
            gap_high = low.iloc[i - 2]
            gap_low = high.iloc[i]
            filled = False
            for j in range(i + 1, n):
                if high.iloc[j] >= gap_low:
                    filled = True
                    break
            gaps.append(FairValueGap(
                index=i - 1, type="bearish",
                high=gap_high, low=gap_low, filled=filled,
            ))
            signal.iloc[i - 1] = -1

    return FairValueGapResult(gaps=gaps, signal=signal)


@dataclass
class OrderBlock:
    index: int
    type: str       # "bullish" or "bearish"
    high: float
    low: float


@dataclass
class OrderBlockResult:
    blocks: list[OrderBlock]
    signal: pd.Series


def order_blocks(
    high: pd.Series, low: pd.Series, close: pd.Series,
    open_: pd.Series, atr_period: int = 14, impulse_atr_mult: float = 2.0,
) -> OrderBlockResult:
    # / last opposite candle before a large impulse move (>N*ATR)
    n = len(close)
    signal = pd.Series(0, index=close.index, dtype=int)
    blocks: list[OrderBlock] = []

    if n < atr_period + 2:
        return OrderBlockResult(blocks=[], signal=signal)

    atr_vals = compute_atr(high, low, close, period=atr_period)

    for i in range(1, n):
        if np.isnan(atr_vals.iloc[i]):
            continue
        threshold = atr_vals.iloc[i] * impulse_atr_mult

        move = close.iloc[i] - close.iloc[i - 1]

        # / bullish impulse: large up move. order block = last bearish candle before
        if move > threshold:
            # / find last bearish candle at or before i-1
            for k in range(i - 1, -1, -1):
                if close.iloc[k] < open_.iloc[k]:
                    blocks.append(OrderBlock(
                        index=k, type="bullish",
                        high=high.iloc[k], low=low.iloc[k],
                    ))
                    signal.iloc[k] = 1
                    break

        # / bearish impulse: large down move
        elif move < -threshold:
            for k in range(i - 1, -1, -1):
                if close.iloc[k] > open_.iloc[k]:
                    blocks.append(OrderBlock(
                        index=k, type="bearish",
                        high=high.iloc[k], low=low.iloc[k],
                    ))
                    signal.iloc[k] = -1
                    break

    return OrderBlockResult(blocks=blocks, signal=signal)


def _find_swing_points(
    high: pd.Series, low: pd.Series, lookback: int = 5,
) -> tuple[pd.Series, pd.Series]:
    # / swing highs: bar where high is highest in lookback window on both sides
    # / swing lows: bar where low is lowest in lookback window on both sides
    n = len(high)
    swing_high = pd.Series(np.nan, index=high.index, dtype=float)
    swing_low = pd.Series(np.nan, index=low.index, dtype=float)

    for i in range(lookback, n - lookback):
        window_high = high.iloc[i - lookback:i + lookback + 1]
        if high.iloc[i] == window_high.max():
            swing_high.iloc[i] = high.iloc[i]

        window_low = low.iloc[i - lookback:i + lookback + 1]
        if low.iloc[i] == window_low.min():
            swing_low.iloc[i] = low.iloc[i]

    return swing_high, swing_low


@dataclass
class StructureBreak:
    index: int
    type: str       # "bos" (break of structure) or "choch" (change of character)
    direction: str  # "bullish" or "bearish"
    level: float    # the swing level that was broken


@dataclass
class StructureBreakResult:
    breaks: list[StructureBreak]
    swing_highs: pd.Series
    swing_lows: pd.Series
    signal: pd.Series


def structure_breaks(
    high: pd.Series, low: pd.Series, close: pd.Series,
    swing_lookback: int = 5,
) -> StructureBreakResult:
    # / bos = break in trend direction, choch = break against trend
    n = len(close)
    signal = pd.Series(0, index=close.index, dtype=int)
    breaks: list[StructureBreak] = []

    swing_highs, swing_lows = _find_swing_points(high, low, swing_lookback)

    if n < swing_lookback * 2 + 1:
        return StructureBreakResult(
            breaks=[], swing_highs=swing_highs,
            swing_lows=swing_lows, signal=signal,
        )

    # / track most recent swing high/low and trend
    last_swing_high: float | None = None
    last_swing_low: float | None = None
    trend = 0  # 1 = bullish, -1 = bearish, 0 = undefined

    for i in range(n):
        # / update swing levels
        if not np.isnan(swing_highs.iloc[i]):
            last_swing_high = swing_highs.iloc[i]
        if not np.isnan(swing_lows.iloc[i]):
            last_swing_low = swing_lows.iloc[i]

        if last_swing_high is None or last_swing_low is None:
            continue

        # / bullish break: close above last swing high
        if close.iloc[i] > last_swing_high:
            if trend == 1:
                break_type = "bos"
            elif trend == -1:
                break_type = "choch"
            else:
                break_type = "bos"

            breaks.append(StructureBreak(
                index=i, type=break_type,
                direction="bullish", level=last_swing_high,
            ))
            signal.iloc[i] = 1
            trend = 1
            last_swing_high = None  # / consumed

        # / bearish break: close below last swing low
        elif close.iloc[i] < last_swing_low:
            if trend == -1:
                break_type = "bos"
            elif trend == 1:
                break_type = "choch"
            else:
                break_type = "bos"

            breaks.append(StructureBreak(
                index=i, type=break_type,
                direction="bearish", level=last_swing_low,
            ))
            signal.iloc[i] = -1
            trend = -1
            last_swing_low = None

    return StructureBreakResult(
        breaks=breaks, swing_highs=swing_highs,
        swing_lows=swing_lows, signal=signal,
    )

# / support/resistance indicators: pivot points, fibonacci, s/r zones
# / all take pandas series, return dataclasses
# / nan-safe: insufficient data returns nan/empty

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class PivotPoints:
    pivot: float
    r1: float
    r2: float
    r3: float
    s1: float
    s2: float
    s3: float


def pivot_points(
    high: float, low: float, close: float,
    method: str = "standard",
) -> PivotPoints:
    # / calculate pivot points from previous period high/low/close
    if method == "fibonacci":
        pivot = (high + low + close) / 3
        diff = high - low
        r1 = pivot + 0.382 * diff
        r2 = pivot + 0.618 * diff
        r3 = pivot + diff
        s1 = pivot - 0.382 * diff
        s2 = pivot - 0.618 * diff
        s3 = pivot - diff
    elif method == "woodie":
        pivot = (high + low + 2 * close) / 4
        r1 = 2 * pivot - low
        r2 = pivot + (high - low)
        r3 = r1 + (high - low)
        s1 = 2 * pivot - high
        s2 = pivot - (high - low)
        s3 = s1 - (high - low)
    else:
        # / standard
        pivot = (high + low + close) / 3
        r1 = 2 * pivot - low
        r2 = pivot + (high - low)
        r3 = high + 2 * (pivot - low)
        s1 = 2 * pivot - high
        s2 = pivot - (high - low)
        s3 = low - 2 * (high - pivot)

    return PivotPoints(pivot=pivot, r1=r1, r2=r2, r3=r3, s1=s1, s2=s2, s3=s3)


@dataclass
class FibonacciLevels:
    swing_high: float
    swing_low: float
    level_236: float
    level_382: float
    level_500: float
    level_618: float
    level_786: float


def fibonacci_retracement(
    high: pd.Series, low: pd.Series, lookback: int = 50,
) -> FibonacciLevels:
    # / fibonacci retracement levels from swing high/low over lookback
    window_high = high.iloc[-lookback:] if len(high) >= lookback else high
    window_low = low.iloc[-lookback:] if len(low) >= lookback else low

    swing_high = float(window_high.max())
    swing_low = float(window_low.min())
    diff = swing_high - swing_low

    if diff == 0:
        # / flat data — all levels equal
        return FibonacciLevels(
            swing_high=swing_high, swing_low=swing_low,
            level_236=swing_high, level_382=swing_high,
            level_500=swing_high, level_618=swing_high,
            level_786=swing_high,
        )

    return FibonacciLevels(
        swing_high=swing_high,
        swing_low=swing_low,
        level_236=swing_high - 0.236 * diff,
        level_382=swing_high - 0.382 * diff,
        level_500=swing_high - 0.500 * diff,
        level_618=swing_high - 0.618 * diff,
        level_786=swing_high - 0.786 * diff,
    )


@dataclass
class SupportResistanceZone:
    level: float
    strength: int    # number of touches
    type: str        # "support", "resistance", or "both"


def sr_zones(
    close: pd.Series, high: pd.Series, low: pd.Series,
    num_zones: int = 5, tolerance_pct: float = 0.02,
) -> list[SupportResistanceZone]:
    # / find s/r zones by clustering price touches
    if len(close) < 2:
        return []

    # / collect all price extremes
    prices = pd.concat([high, low]).reset_index(drop=True)
    prices = prices.dropna().sort_values().values

    if len(prices) == 0:
        return []

    # / cluster prices within tolerance
    clusters: list[list[float]] = []
    current_cluster: list[float] = [prices[0]]

    for p in prices[1:]:
        if current_cluster and abs(p - current_cluster[0]) / current_cluster[0] <= tolerance_pct:
            current_cluster.append(p)
        else:
            clusters.append(current_cluster)
            current_cluster = [p]
    clusters.append(current_cluster)

    # / sort by cluster size (most touches first)
    clusters.sort(key=len, reverse=True)

    # / take top N
    zones: list[SupportResistanceZone] = []
    current_price = float(close.iloc[-1])

    for cluster in clusters[:num_zones]:
        level = float(np.mean(cluster))
        strength = len(cluster)

        if level < current_price:
            zone_type = "support"
        elif level > current_price:
            zone_type = "resistance"
        else:
            zone_type = "both"

        zones.append(SupportResistanceZone(
            level=level, strength=strength, type=zone_type,
        ))

    return zones


def sr_zones_series(
    close: pd.Series, high: pd.Series, low: pd.Series,
    num_zones: int = 5, tolerance_pct: float = 0.02,
) -> pd.Series:
    # / per-bar distance to nearest s/r zone (negative = below, positive = above)
    zones = sr_zones(close, high, low, num_zones, tolerance_pct)
    if not zones:
        return pd.Series(0.0, index=close.index, dtype=float)

    levels = np.array([z.level for z in zones])

    result = pd.Series(0.0, index=close.index, dtype=float)
    for i in range(len(close)):
        price = close.iloc[i]
        if np.isnan(price):
            result.iloc[i] = np.nan
            continue
        dists = price - levels
        nearest_idx = np.argmin(np.abs(dists))
        result.iloc[i] = dists[nearest_idx] / levels[nearest_idx] if levels[nearest_idx] != 0 else 0.0

    return result

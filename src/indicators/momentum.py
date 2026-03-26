# / momentum indicators: rsi, stochastic, cci, williams %r, roc
# / all take pandas series, return series
# / nan-safe: insufficient data returns nan

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    # / relative strength index (0-100)
    # / rsi > 70 = overbought, rsi < 30 = oversold
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    # / wilder smoothing
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


@dataclass
class StochasticResult:
    k: pd.Series  # fast %k
    d: pd.Series  # slow %d (signal)


def stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    d_period: int = 3,
) -> StochasticResult:
    # / stochastic oscillator (0-100)
    # / %k > 80 = overbought, %k < 20 = oversold
    lowest = low.rolling(window=k_period, min_periods=k_period).min()
    highest = high.rolling(window=k_period, min_periods=k_period).max()
    denom = highest - lowest
    k = 100 * (close - lowest) / denom.replace(0, np.nan)
    d = k.rolling(window=d_period, min_periods=d_period).mean()
    return StochasticResult(k=k, d=d)


def cci(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 20,
) -> pd.Series:
    # / commodity channel index
    # / cci > 100 = overbought, cci < -100 = oversold
    tp = (high + low + close) / 3
    sma_tp = tp.rolling(window=period, min_periods=period).mean()
    mad = tp.rolling(window=period, min_periods=period).apply(
        lambda x: np.mean(np.abs(x - np.mean(x))), raw=True,
    )
    return (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))


def williams_r(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    # / williams %r (-100 to 0)
    # / %r > -20 = overbought, %r < -80 = oversold
    highest = high.rolling(window=period, min_periods=period).max()
    lowest = low.rolling(window=period, min_periods=period).min()
    denom = highest - lowest
    return -100 * (highest - close) / denom.replace(0, np.nan)


def roc(series: pd.Series, period: int = 12) -> pd.Series:
    # / rate of change (percentage)
    prev = series.shift(period)
    return ((series - prev) / prev.replace(0, np.nan)) * 100

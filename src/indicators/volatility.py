# / volatility indicators: bollinger bands, atr, keltner channel
# / all take pandas series, return series or dataclasses
# / nan-safe: insufficient data returns nan

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import structlog

from .trend import ema, sma, true_range

logger = structlog.get_logger(__name__)


@dataclass
class BollingerBands:
    upper: pd.Series
    middle: pd.Series
    lower: pd.Series
    bandwidth: pd.Series  # (upper - lower) / middle
    pct_b: pd.Series      # (close - lower) / (upper - lower)


def bollinger_bands(
    series: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> BollingerBands:
    # / bollinger bands: sma +/- n standard deviations
    middle = sma(series, period)
    rolling_std = series.rolling(window=period, min_periods=period).std()
    upper = middle + std_dev * rolling_std
    lower = middle - std_dev * rolling_std
    band_width = upper - lower
    bandwidth = band_width / middle.replace(0, np.nan)
    pct_b = (series - lower) / band_width.replace(0, np.nan)
    return BollingerBands(
        upper=upper, middle=middle, lower=lower,
        bandwidth=bandwidth, pct_b=pct_b,
    )


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    # / average true range — volatility measure in price units
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


@dataclass
class KeltnerChannel:
    upper: pd.Series
    middle: pd.Series
    lower: pd.Series


def keltner_channel(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    ema_period: int = 20,
    atr_period: int = 10,
    multiplier: float = 2.0,
) -> KeltnerChannel:
    # / keltner channel: ema +/- n * atr
    middle = ema(close, ema_period)
    atr_val = atr(high, low, close, atr_period)
    upper = middle + multiplier * atr_val
    lower = middle - multiplier * atr_val
    return KeltnerChannel(upper=upper, middle=middle, lower=lower)

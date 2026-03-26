# / trend indicators: sma, ema, macd, adx, supertrend
# / all functions take pandas series/dataframe, return series
# / nan-safe: returns nan for insufficient data rather than erroring

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


def sma(series: pd.Series, period: int = 20) -> pd.Series:
    # / simple moving average
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int = 20) -> pd.Series:
    # / exponential moving average
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


@dataclass
class MACDResult:
    macd_line: pd.Series
    signal_line: pd.Series
    histogram: pd.Series


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> MACDResult:
    # / moving average convergence divergence
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line
    return MACDResult(macd_line=macd_line, signal_line=signal_line, histogram=histogram)


def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    # / average directional index — measures trend strength (0-100)
    # / adx > 25 = trending, adx < 20 = ranging
    plus_dm = high.diff()
    minus_dm = -low.diff()

    # / +dm is positive only when it's larger than -dm and positive
    plus_dm = pd.Series(
        np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0),
        index=high.index,
    )

    tr = true_range(high, low, close)

    # / wilder smoothing (equivalent to ema with alpha=1/period)
    atr_smooth = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr_smooth
    minus_di = 100 * minus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr_smooth

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return adx_val


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    # / true range = max(high-low, |high-prev_close|, |low-prev_close|)
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


@dataclass
class SupertrendResult:
    supertrend: pd.Series
    direction: pd.Series  # 1 = uptrend, -1 = downtrend


def supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> SupertrendResult:
    # / supertrend: atr-based trailing stop that flips direction
    tr = true_range(high, low, close)
    atr_val = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    hl2 = (high + low) / 2
    upper_band = hl2 + multiplier * atr_val
    lower_band = hl2 - multiplier * atr_val

    st = pd.Series(np.nan, index=close.index, dtype=float)
    direction = pd.Series(1, index=close.index, dtype=int)

    for i in range(1, len(close)):
        if np.isnan(atr_val.iloc[i]):
            continue

        # / carry forward bands with trend logic
        prev_lb = lower_band.iloc[i - 1] if not np.isnan(st.iloc[i - 1]) else lower_band.iloc[i]
        prev_ub = upper_band.iloc[i - 1] if not np.isnan(st.iloc[i - 1]) else upper_band.iloc[i]

        if lower_band.iloc[i] > prev_lb or close.iloc[i - 1] < prev_lb:
            lower_band.iloc[i] = lower_band.iloc[i]
        else:
            lower_band.iloc[i] = prev_lb

        if upper_band.iloc[i] < prev_ub or close.iloc[i - 1] > prev_ub:
            upper_band.iloc[i] = upper_band.iloc[i]
        else:
            upper_band.iloc[i] = prev_ub

        prev_st = st.iloc[i - 1]
        if np.isnan(prev_st):
            # / first valid bar
            if close.iloc[i] > upper_band.iloc[i]:
                st.iloc[i] = lower_band.iloc[i]
                direction.iloc[i] = 1
            else:
                st.iloc[i] = upper_band.iloc[i]
                direction.iloc[i] = -1
        elif prev_st == upper_band.iloc[i - 1] if not np.isnan(upper_band.iloc[i - 1]) else False:
            # / was in downtrend
            if close.iloc[i] > upper_band.iloc[i]:
                st.iloc[i] = lower_band.iloc[i]
                direction.iloc[i] = 1
            else:
                st.iloc[i] = upper_band.iloc[i]
                direction.iloc[i] = -1
        else:
            # / was in uptrend
            if close.iloc[i] < lower_band.iloc[i]:
                st.iloc[i] = upper_band.iloc[i]
                direction.iloc[i] = -1
            else:
                st.iloc[i] = lower_band.iloc[i]
                direction.iloc[i] = 1

    return SupertrendResult(supertrend=st, direction=direction)

# / trend indicators: sma, ema, macd, adx, supertrend
# / all functions take pandas series/dataframe, return series
# / nan-safe: returns nan for insufficient data rather than erroring

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


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


@dataclass
class DonchianResult:
    upper: pd.Series
    lower: pd.Series
    middle: pd.Series


def donchian_channel(high: pd.Series, low: pd.Series, period: int = 20) -> DonchianResult:
    # / donchian channel: N-period highest high and lowest low
    upper = high.rolling(window=period).max()
    lower = low.rolling(window=period).min()
    middle = (upper + lower) / 2
    return DonchianResult(upper=upper, lower=lower, middle=middle)


@dataclass
class IchimokuResult:
    conversion: pd.Series  # tenkan-sen (9-period)
    base: pd.Series        # kijun-sen (26-period)
    span_a: pd.Series      # senkou span a (displaced +26)
    span_b: pd.Series      # senkou span b (52-period, displaced +26)
    lagging: pd.Series     # chikou span (close shifted -26)


def ichimoku(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    conversion_period: int = 9,
    base_period: int = 26,
    span_b_period: int = 52,
    displacement: int = 26,
) -> IchimokuResult:
    # / ichimoku cloud: five lines — tenkan (fast), kijun (slow), spans a/b (cloud), chikou (lagging close)
    conversion = (high.rolling(conversion_period).max() + low.rolling(conversion_period).min()) / 2
    base = (high.rolling(base_period).max() + low.rolling(base_period).min()) / 2
    span_a = ((conversion + base) / 2).shift(displacement)
    span_b = ((high.rolling(span_b_period).max() + low.rolling(span_b_period).min()) / 2).shift(displacement)
    lagging = close.shift(-displacement)
    return IchimokuResult(conversion=conversion, base=base, span_a=span_a, span_b=span_b, lagging=lagging)


@dataclass
class PSARResult:
    sar: pd.Series
    direction: pd.Series  # 1 = long trend, -1 = short trend


def psar(
    high: pd.Series,
    low: pd.Series,
    step: float = 0.02,
    max_step: float = 0.2,
) -> PSARResult:
    # / parabolic sar (wilder). direction flips when price crosses sar.
    # / direction is float dtype so length<2 / warmup positions stay nan (not 0)
    n = len(high)
    sar = pd.Series(np.nan, index=high.index, dtype=float)
    direction = pd.Series(np.nan, index=high.index, dtype=float)
    if n < 2:
        return PSARResult(sar=sar, direction=direction)

    # / seed direction from first two highs (up if high[1] >= high[0])
    trend_up = high.iloc[1] >= high.iloc[0]
    ep = high.iloc[0] if trend_up else low.iloc[0]
    af = step
    current = low.iloc[0] if trend_up else high.iloc[0]
    sar.iloc[0] = current
    direction.iloc[0] = 1.0 if trend_up else -1.0

    for i in range(1, n):
        prev_sar = current
        current = prev_sar + af * (ep - prev_sar)
        if trend_up:
            # / sar cannot exceed prior two lows
            current = min(current, low.iloc[i - 1], low.iloc[max(0, i - 2)])
            if low.iloc[i] < current:
                # / flip to short
                trend_up = False
                current = ep
                ep = low.iloc[i]
                af = step
            else:
                if high.iloc[i] > ep:
                    ep = high.iloc[i]
                    af = min(af + step, max_step)
        else:
            # / sar cannot fall below prior two highs
            current = max(current, high.iloc[i - 1], high.iloc[max(0, i - 2)])
            if high.iloc[i] > current:
                # / flip to long
                trend_up = True
                current = ep
                ep = high.iloc[i]
                af = step
            else:
                if low.iloc[i] < ep:
                    ep = low.iloc[i]
                    af = min(af + step, max_step)
        sar.iloc[i] = current
        direction.iloc[i] = 1.0 if trend_up else -1.0
    return PSARResult(sar=sar, direction=direction)

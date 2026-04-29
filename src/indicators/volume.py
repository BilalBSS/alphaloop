
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = pd.Series(np.where(close > close.shift(1), 1, np.where(close < close.shift(1), -1, 0)), index=close.index)
    return (volume * direction).cumsum()


def vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    dates: pd.Series | None = None,
) -> pd.Series:
    tp = (high + low + close) / 3
    tp_vol = tp * volume
    if dates is not None:
        cum_tp_vol = tp_vol.groupby(dates).cumsum()
        cum_vol = volume.groupby(dates).cumsum()
    else:
        cum_tp_vol = tp_vol.cumsum()
        cum_vol = volume.cumsum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


@dataclass
class VolumeProfile:
    price_levels: pd.Series  # price bins
    volume_at_price: pd.Series  # volume per bin
    poc: float  # / point of control (highest
    value_area_high: float
    value_area_low: float


def volume_profile(
    close: pd.Series,
    volume: pd.Series,
    num_bins: int = 50,
    value_area_pct: float = 0.70,
) -> VolumeProfile:
    price_min = close.min()
    price_max = close.max()

    if price_min == price_max or len(close) == 0:
        return VolumeProfile(
            price_levels=pd.Series(dtype=float),
            volume_at_price=pd.Series(dtype=float),
            poc=float(price_min) if len(close) > 0 else 0.0,
            value_area_high=float(price_max) if len(close) > 0 else 0.0,
            value_area_low=float(price_min) if len(close) > 0 else 0.0,
        )

    bins = np.linspace(price_min, price_max, num_bins + 1)
    bin_centers = (bins[:-1] + bins[1:]) / 2
    bin_indices = np.digitize(close.to_numpy(), bins) - 1
    bin_indices = np.clip(bin_indices, 0, num_bins - 1)

    vol_at_price = np.zeros(num_bins)
    for i, idx in enumerate(bin_indices):
        vol_at_price[idx] += volume.iloc[i]

    poc_idx = np.argmax(vol_at_price)
    poc = float(bin_centers[poc_idx])

    total_vol = vol_at_price.sum()
    if total_vol == 0:
        return VolumeProfile(
            price_levels=pd.Series(bin_centers),
            volume_at_price=pd.Series(vol_at_price),
            poc=poc,
            value_area_high=poc,
            value_area_low=poc,
        )

    target_vol = total_vol * value_area_pct
    va_vol = vol_at_price[poc_idx]
    lo = poc_idx
    hi = poc_idx

    while va_vol < target_vol and (lo > 0 or hi < num_bins - 1):
        vol_below = vol_at_price[lo - 1] if lo > 0 else 0
        vol_above = vol_at_price[hi + 1] if hi < num_bins - 1 else 0

        if vol_below >= vol_above and lo > 0:
            lo -= 1
            va_vol += vol_at_price[lo]
        elif hi < num_bins - 1:
            hi += 1
            va_vol += vol_at_price[hi]
        elif lo > 0:
            lo -= 1
            va_vol += vol_at_price[lo]
        else:
            break

    return VolumeProfile(
        price_levels=pd.Series(bin_centers),
        volume_at_price=pd.Series(vol_at_price),
        poc=poc,
        value_area_high=float(bin_centers[hi]),
        value_area_low=float(bin_centers[lo]),
    )


def mfi(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int = 14,
) -> pd.Series:
    tp = (high + low + close) / 3
    raw_mf = tp * volume

    positive_mf = pd.Series(
        np.where(tp > tp.shift(1), raw_mf, 0.0),
        index=close.index,
    )
    negative_mf = pd.Series(
        np.where(tp < tp.shift(1), raw_mf, 0.0),
        index=close.index,
    )

    pos_sum = positive_mf.rolling(window=period, min_periods=period).sum()
    neg_sum = negative_mf.rolling(window=period, min_periods=period).sum()

    mf_ratio = pos_sum / neg_sum.replace(0, np.nan)
    return 100 - (100 / (1 + mf_ratio))


def anchored_vwap(
    high: pd.Series, low: pd.Series, close: pd.Series,
    volume: pd.Series, anchor_idx: int = 0,
) -> pd.Series:
    typical = (high + low + close) / 3
    result = pd.Series(index=close.index, dtype=float)
    result[:] = np.nan
    cum_vol = 0.0
    cum_tp_vol = 0.0
    for i in range(anchor_idx, len(close)):
        cum_vol += volume.iloc[i]
        cum_tp_vol += typical.iloc[i] * volume.iloc[i]
        result.iloc[i] = cum_tp_vol / cum_vol if cum_vol > 0 else typical.iloc[i]
    return result

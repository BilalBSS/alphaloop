# / market breadth indicators computed from equity universe
# / advance/decline line, mcclellan oscillator, new highs/lows

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class BreadthData:
    ad_line: pd.Series
    mcclellan: pd.Series
    new_highs: pd.Series
    new_lows: pd.Series
    pct_above_200sma: pd.Series


def compute_breadth(symbol_data: dict[str, pd.DataFrame]) -> BreadthData | None:
    # / compute breadth from dict of symbol -> OHLCV dataframes
    if not symbol_data:
        return None

    all_dates = sorted(set().union(*(df.index for df in symbol_data.values())))
    if not all_dates:
        return None

    advances = pd.Series(0, index=all_dates, dtype=int)
    declines = pd.Series(0, index=all_dates, dtype=int)
    highs_52w = pd.Series(0, index=all_dates, dtype=int)
    lows_52w = pd.Series(0, index=all_dates, dtype=int)
    above_200 = pd.Series(0, index=all_dates, dtype=int)
    total = pd.Series(0, index=all_dates, dtype=int)

    for sym, df in symbol_data.items():
        if len(df) < 2 or "close" not in df.columns:
            continue

        ret = df["close"].pct_change()

        for d in df.index:
            if d not in advances.index:
                continue
            idx = df.index.get_loc(d)
            if idx == 0:
                continue

            total[d] += 1
            if ret.iloc[idx] > 0:
                advances[d] += 1
            elif ret.iloc[idx] < 0:
                declines[d] += 1

            # / 52-week high/low (252 trading days)
            lookback = max(0, idx - 252)
            high_252 = df["close"].iloc[lookback:idx + 1].max()
            low_252 = df["close"].iloc[lookback:idx + 1].min()
            if df["close"].iloc[idx] >= high_252 * 0.99:
                highs_52w[d] += 1
            if df["close"].iloc[idx] <= low_252 * 1.01:
                lows_52w[d] += 1

            # / above 200 SMA
            if idx >= 200:
                sma200 = df["close"].iloc[idx - 200:idx].mean()
                if df["close"].iloc[idx] > sma200:
                    above_200[d] += 1

    ad_diff = advances - declines
    ad_line = ad_diff.cumsum()

    # / mcclellan oscillator = 19-day EMA(A-D) - 39-day EMA(A-D)
    ad_float = ad_diff.astype(float)
    ema19 = ad_float.ewm(span=19, adjust=False).mean()
    ema39 = ad_float.ewm(span=39, adjust=False).mean()
    mcclellan = ema19 - ema39

    pct_above = (above_200 / total.replace(0, 1) * 100).fillna(0)

    return BreadthData(
        ad_line=ad_line,
        mcclellan=mcclellan,
        new_highs=highs_52w,
        new_lows=lows_52w,
        pct_above_200sma=pct_above,
    )

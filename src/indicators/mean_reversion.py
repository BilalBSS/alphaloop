# / mean reversion detection via hurst exponent
# / H < 0.5 = mean-reverting, H > 0.5 = trending, H = 0.5 = random walk

from __future__ import annotations

import numpy as np
import pandas as pd


def hurst_exponent(prices: pd.Series, max_lag: int = 20) -> float:
    # / rescaled range (R/S) analysis
    if len(prices) < max_lag * 2:
        return 0.5

    log_prices = np.log(prices.values)
    lags = range(2, max_lag + 1)
    rs_values = []

    for lag in lags:
        rs_lag = []
        for start in range(0, len(log_prices) - lag, lag):
            chunk = log_prices[start:start + lag]
            mean = chunk.mean()
            deviate = chunk - mean
            cumdev = np.cumsum(deviate)
            r = cumdev.max() - cumdev.min()
            s = chunk.std(ddof=1)
            if s > 0:
                rs_lag.append(r / s)
        if rs_lag:
            rs_values.append((np.log(lag), np.log(np.mean(rs_lag))))

    if len(rs_values) < 3:
        return 0.5

    x = np.array([v[0] for v in rs_values])
    y = np.array([v[1] for v in rs_values])
    slope, _ = np.polyfit(x, y, 1)
    return float(np.clip(slope, 0.0, 1.0))


def rolling_hurst(prices: pd.Series, window: int = 100, max_lag: int = 20) -> pd.Series:
    # / rolling hurst exponent over a window
    result = pd.Series(index=prices.index, dtype=float)
    result[:] = np.nan
    for i in range(window, len(prices)):
        chunk = prices.iloc[i - window:i]
        result.iloc[i] = hurst_exponent(chunk, max_lag=max_lag)
    return result


def classify_regime_hurst(h: float) -> str:
    # / classify based on hurst value
    if h < 0.4:
        return "mean_reverting"
    elif h > 0.6:
        return "trending"
    return "random_walk"

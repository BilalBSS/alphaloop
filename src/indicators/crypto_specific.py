# / crypto-specific indicators: funding rate, open interest, exchange flows, nvt
# / pure math on pre-fetched data — no api calls
# / all take pandas series, return series

from __future__ import annotations

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


def funding_rate_signal(
    funding_rates: pd.Series, threshold: float = 0.01,
) -> pd.Series:
    # / contrarian signal: extreme positive funding = bearish, extreme negative = bullish
    # / 1 = bullish (negative funding, shorts paying), -1 = bearish, 0 = neutral
    signal = pd.Series(0, index=funding_rates.index, dtype=int)
    signal[funding_rates < -threshold] = 1
    signal[funding_rates > threshold] = -1
    return signal


def open_interest_trend(
    oi: pd.Series, price: pd.Series, period: int = 14,
) -> pd.Series:
    # / rising OI + rising price = bullish (new longs)
    # / rising OI + falling price = bearish (new shorts)
    # / falling OI = position closing (neutral)
    oi_change = oi.pct_change(period)
    price_change = price.pct_change(period)

    signal = pd.Series(0.0, index=oi.index, dtype=float)
    rising_oi = oi_change > 0

    signal[rising_oi & (price_change > 0)] = 1.0
    signal[rising_oi & (price_change < 0)] = -1.0

    return signal


def exchange_flow_ratio(
    inflows: pd.Series, outflows: pd.Series, period: int = 7,
) -> pd.Series:
    # / outflow > inflow = bullish (coins leaving exchanges = accumulation)
    # / returns smoothed ratio: values > 1 = bullish, < 1 = bearish
    ratio = outflows / inflows.replace(0, np.nan)
    smoothed = ratio.rolling(window=period, min_periods=1).mean()
    return smoothed


def nvt_ratio(
    market_cap: pd.Series, tx_volume: pd.Series,
) -> pd.Series:
    # / network value to transactions — crypto "P/E ratio"
    # / high nvt = overvalued, low nvt = undervalued
    return market_cap / tx_volume.replace(0, np.nan)


def nvt_signal(
    market_cap: pd.Series, tx_volume: pd.Series, period: int = 90,
) -> pd.Series:
    # / z-score of nvt against rolling mean
    nvt = nvt_ratio(market_cap, tx_volume)
    rolling_mean = nvt.rolling(window=period, min_periods=period).mean()
    rolling_std = nvt.rolling(window=period, min_periods=period).std()
    z = (nvt - rolling_mean) / rolling_std.replace(0, np.nan)
    return z

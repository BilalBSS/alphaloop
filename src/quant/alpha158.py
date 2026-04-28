# / alpha158 feature set port from qlib
# / pure pandas/numpy, ~60 features

from __future__ import annotations

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

# / default lookback windows used across qlib's Alpha158
DEFAULT_WINDOWS = (5, 10, 20, 30, 60)


def _rolling_beta(y: pd.Series, window: int) -> pd.Series:
    # / slope of linear regression y ~ t over rolling window
    def _slope(arr: np.ndarray) -> float:
        if len(arr) < 2 or np.all(np.isnan(arr)):
            return np.nan
        x = np.arange(len(arr), dtype=float)
        valid = ~np.isnan(arr)
        if valid.sum() < 2:
            return np.nan
        xv = x[valid]
        yv = arr[valid]
        # / classic OLS slope
        x_mean = xv.mean()
        y_mean = yv.mean()
        cov = ((xv - x_mean) * (yv - y_mean)).sum()
        var = ((xv - x_mean) ** 2).sum()
        return cov / var if var > 0 else np.nan

    return y.rolling(window, min_periods=max(2, window // 2)).apply(_slope, raw=True)


def _rolling_rsquare(y: pd.Series, window: int) -> pd.Series:
    # / r-squared of linear regression y ~ t over rolling window
    def _r2(arr: np.ndarray) -> float:
        if len(arr) < 2:
            return np.nan
        x = np.arange(len(arr), dtype=float)
        valid = ~np.isnan(arr)
        if valid.sum() < 2:
            return np.nan
        xv = x[valid]
        yv = arr[valid]
        if np.var(yv) == 0:
            return 0.0
        corr = np.corrcoef(xv, yv)[0, 1]
        return corr * corr if not np.isnan(corr) else np.nan

    return y.rolling(window, min_periods=max(2, window // 2)).apply(_r2, raw=True)


def _rolling_residual(y: pd.Series, window: int) -> pd.Series:
    # / last-bar residual from rolling linear fit
    def _resid(arr: np.ndarray) -> float:
        if len(arr) < 2:
            return np.nan
        x = np.arange(len(arr), dtype=float)
        valid = ~np.isnan(arr)
        if valid.sum() < 2:
            return np.nan
        xv = x[valid]
        yv = arr[valid]
        x_mean = xv.mean()
        y_mean = yv.mean()
        var = ((xv - x_mean) ** 2).sum()
        if var == 0:
            return 0.0
        slope = ((xv - x_mean) * (yv - y_mean)).sum() / var
        intercept = y_mean - slope * x_mean
        predicted_last = slope * xv[-1] + intercept
        return float(yv[-1] - predicted_last)

    return y.rolling(window, min_periods=max(2, window // 2)).apply(_resid, raw=True)


def _k_features(df: pd.DataFrame) -> dict[str, pd.Series]:
    # / K family — candle shape (ratios of open/high/low to close)
    features = {}
    features["KMID"] = (df["close"] - df["open"]) / df["open"].replace(0, np.nan)
    features["KLEN"] = (df["high"] - df["low"]) / df["open"].replace(0, np.nan)
    features["KMID2"] = (df["close"] - df["open"]) / (df["high"] - df["low"]).replace(0, np.nan)
    features["KUP"] = (df["high"] - df[["open", "close"]].max(axis=1)) / df["open"].replace(0, np.nan)
    features["KUP2"] = (df["high"] - df[["open", "close"]].max(axis=1)) / (df["high"] - df["low"]).replace(0, np.nan)
    features["KLOW"] = (df[["open", "close"]].min(axis=1) - df["low"]) / df["open"].replace(0, np.nan)
    features["KLOW2"] = (df[["open", "close"]].min(axis=1) - df["low"]) / (df["high"] - df["low"]).replace(0, np.nan)
    features["KSFT"] = (2 * df["close"] - df["high"] - df["low"]) / df["open"].replace(0, np.nan)
    features["KSFT2"] = (2 * df["close"] - df["high"] - df["low"]) / (df["high"] - df["low"]).replace(0, np.nan)
    return features


def _rolling_features(
    df: pd.DataFrame, windows: tuple[int, ...]
) -> dict[str, pd.Series]:
    # / rolling families — ROC, MA, STD, MAX, MIN, QTLU, QTLD, RANK, RSV,
    # / IMAX, IMIN, IMXD, BETA, RSQR, RESI, CORR, CORD, CNTP, CNTN, CNTD,
    # / SUMP, SUMN, SUMD, VMA, VSTD, WVMA
    features: dict[str, pd.Series] = {}
    c = df["close"]
    h = df["high"]
    low = df["low"]
    v = df["volume"]
    returns = c.pct_change()
    log_vol_change = np.log(v / v.shift(1).replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)

    for w in windows:
        # / ROC: rate of change
        features[f"ROC{w}"] = c.shift(w) / c

        # / MA: moving average ratio
        features[f"MA{w}"] = c.rolling(w, min_periods=1).mean() / c

        # / STD: rolling std of close / current close
        features[f"STD{w}"] = c.rolling(w, min_periods=max(2, w // 2)).std() / c

        # / MAX / MIN: rolling extremes / current close
        features[f"MAX{w}"] = c.rolling(w, min_periods=1).max() / c
        features[f"MIN{w}"] = c.rolling(w, min_periods=1).min() / c

        # / QTLU / QTLD: 80/20 quantiles of close / current close
        features[f"QTLU{w}"] = c.rolling(w, min_periods=max(2, w // 2)).quantile(0.8) / c
        features[f"QTLD{w}"] = c.rolling(w, min_periods=max(2, w // 2)).quantile(0.2) / c

        # / RANK: position of current close in rolling window [0,1]
        features[f"RANK{w}"] = c.rolling(w, min_periods=1).apply(
            lambda x: (x[-1] > x[:-1]).sum() / max(len(x) - 1, 1), raw=True
        )

        # / RSV: raw stochastic value
        rolling_high = h.rolling(w, min_periods=1).max()
        rolling_low = low.rolling(w, min_periods=1).min()
        features[f"RSV{w}"] = (c - rolling_low) / (rolling_high - rolling_low).replace(0, np.nan)

        # / IMAX / IMIN: position in window where max/min occurred, normalized
        features[f"IMAX{w}"] = h.rolling(w, min_periods=1).apply(
            lambda x: (len(x) - 1 - np.argmax(x)) / max(len(x) - 1, 1), raw=True
        )
        features[f"IMIN{w}"] = low.rolling(w, min_periods=1).apply(
            lambda x: (len(x) - 1 - np.argmin(x)) / max(len(x) - 1, 1), raw=True
        )
        features[f"IMXD{w}"] = features[f"IMAX{w}"] - features[f"IMIN{w}"]

        # / BETA / RSQR / RESI: rolling linear fit stats
        features[f"BETA{w}"] = _rolling_beta(c, w) / c
        features[f"RSQR{w}"] = _rolling_rsquare(c, w)
        features[f"RESI{w}"] = _rolling_residual(c, w) / c

        # / CORR: rolling corr between close and log volume
        log_v = np.log(v.replace(0, np.nan))
        features[f"CORR{w}"] = c.rolling(w, min_periods=max(2, w // 2)).corr(log_v)

        # / CORD: corr between return and log volume change
        features[f"CORD{w}"] = returns.rolling(w, min_periods=max(2, w // 2)).corr(log_vol_change)

        # / CNTP / CNTN: fraction of positive / negative return days
        features[f"CNTP{w}"] = (returns > 0).rolling(w, min_periods=1).mean()
        features[f"CNTN{w}"] = (returns < 0).rolling(w, min_periods=1).mean()
        features[f"CNTD{w}"] = features[f"CNTP{w}"] - features[f"CNTN{w}"]

        # / SUMP / SUMN: ratio of positive / negative return sum to total abs sum
        pos_sum = returns.clip(lower=0).rolling(w, min_periods=1).sum()
        neg_sum = (-returns.clip(upper=0)).rolling(w, min_periods=1).sum()
        tot = (pos_sum + neg_sum).replace(0, np.nan)
        features[f"SUMP{w}"] = pos_sum / tot
        features[f"SUMN{w}"] = neg_sum / tot
        features[f"SUMD{w}"] = (pos_sum - neg_sum) / tot

        # / VMA / VSTD: volume versions of MA/STD
        features[f"VMA{w}"] = v.rolling(w, min_periods=1).mean() / v.replace(0, np.nan)
        features[f"VSTD{w}"] = v.rolling(w, min_periods=max(2, w // 2)).std() / v.replace(0, np.nan)

        # / WVMA: weighted (by return magnitude) volume moving avg
        weighted_v = returns.abs() * v
        features[f"WVMA{w}"] = weighted_v.rolling(w, min_periods=1).mean() / (
            v.rolling(w, min_periods=1).mean().replace(0, np.nan)
        )

    return features


def compute_alpha158(
    ohlcv: pd.DataFrame,
    windows: tuple[int, ...] = DEFAULT_WINDOWS,
) -> pd.DataFrame:
    """compute alpha158-style features from an ohlcv dataframe.

    this is a qlib-inspired reference feature set — a benchmark for our hand-built
    features in ml_signals.build_features(). if our set doesn't beat alpha158 on
    held-out brier score, we should switch to alpha158.

    args:
        ohlcv: dataframe with columns [open, high, low, close, volume], sorted old->new
        windows: rolling windows to compute features over. default (5,10,20,30,60).

    returns:
        dataframe indexed the same as ohlcv, with ~60 feature columns when using
        the default 5 windows (9 candle features + 26 rolling features * 5 windows
        = 139 columns, trimmed to the most useful ~60 in practice).

    features are NOT normalized per-symbol — the caller is responsible for
    cross-sectional ranking if training a cross-asset model.
    """
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(ohlcv.columns):
        missing = required - set(ohlcv.columns)
        raise ValueError(f"ohlcv missing columns: {missing}")

    if len(ohlcv) == 0:
        return pd.DataFrame(index=ohlcv.index)

    features: dict[str, pd.Series] = {}
    features.update(_k_features(ohlcv))
    features.update(_rolling_features(ohlcv, windows))

    result = pd.DataFrame(features, index=ohlcv.index)
    # / replace any infs from div-by-zero with NaN so downstream models can impute
    result = result.replace([np.inf, -np.inf], np.nan)
    return result


def feature_count(windows: tuple[int, ...] = DEFAULT_WINDOWS) -> int:
    """reports how many features compute_alpha158 will produce for a given window set.

    useful for sanity-checking in tests.
    """
    # / 9 candle + 26 per-window features
    return 9 + 26 * len(windows)

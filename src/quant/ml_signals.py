# / ml-based signal generation using lightgbm
# / trains on indicator features, predicts forward return direction

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class MlPrediction:
    symbol: str
    probability: float
    feature_importance: dict
    model_version: str


def build_features(
    ohlcv: pd.DataFrame,
    indicators: dict | None = None,
    fundamentals: dict | None = None,
) -> pd.DataFrame:
    # / construct feature matrix from ohlcv + optional data
    df = ohlcv.copy()

    df["ret_1d"] = df["close"].pct_change(1)
    df["ret_5d"] = df["close"].pct_change(5)
    df["ret_20d"] = df["close"].pct_change(20)
    df["vol_20d"] = df["ret_1d"].rolling(20).std()
    df["vol_ratio"] = df["ret_1d"].rolling(5).std() / df["ret_1d"].rolling(20).std()
    df["high_low_range"] = (df["high"] - df["low"]) / df["close"]
    df["close_vs_sma20"] = df["close"] / df["close"].rolling(20).mean() - 1
    df["close_vs_sma50"] = df["close"] / df["close"].rolling(50).mean() - 1
    df["volume_ratio"] = df["volume"] / df["volume"].rolling(20).mean()

    # / rsi
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.inf)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    # / macd
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    if indicators:
        for key, value in indicators.items():
            if isinstance(value, (int, float)):
                df[f"ind_{key}"] = value

    if fundamentals:
        for key, value in fundamentals.items():
            if isinstance(value, (int, float)):
                df[f"fund_{key}"] = value

    return df.dropna()


def build_target(ohlcv: pd.DataFrame, forward_days: int = 5) -> pd.Series:
    # / binary target: 1 if forward return > 0
    fwd_ret = ohlcv["close"].shift(-forward_days) / ohlcv["close"] - 1
    return (fwd_ret > 0).astype(int)


async def train_and_predict(
    ohlcv: pd.DataFrame,
    indicators: dict | None = None,
    fundamentals: dict | None = None,
    train_window: int = 252,
    forward_days: int = 5,
) -> MlPrediction | None:
    # / train on historical data, predict latest
    try:
        import lightgbm as lgb
    except ImportError:
        logger.warning("lightgbm_not_installed")
        return None

    features = build_features(ohlcv, indicators, fundamentals)
    target = build_target(ohlcv, forward_days)

    common_idx = features.index.intersection(target.dropna().index)
    if len(common_idx) < train_window + forward_days:
        return None

    features = features.loc[common_idx]
    target = target.loc[common_idx]

    feature_cols = [c for c in features.columns if c not in ["open", "high", "low", "close", "volume"]]

    X_train = features[feature_cols].iloc[-(train_window + forward_days):-forward_days].values
    y_train = target.iloc[-(train_window + forward_days):-forward_days].values
    X_latest = features[feature_cols].iloc[-1:].values

    if len(X_train) < 100 or y_train.sum() < 10 or (len(y_train) - y_train.sum()) < 10:
        return None

    def _fit():
        params = {
            "objective": "binary",
            "metric": "binary_logloss",
            "num_leaves": 31,
            "learning_rate": 0.05,
            "n_estimators": 100,
            "min_child_samples": 20,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "verbose": -1,
        }
        model = lgb.LGBMClassifier(**params)
        model.fit(X_train, y_train)
        prob = model.predict_proba(X_latest)[0][1]
        importance = dict(zip(feature_cols, model.feature_importances_.tolist()))
        top5 = dict(sorted(importance.items(), key=lambda x: x[1], reverse=True)[:5])
        return prob, top5

    prob, importance = await asyncio.to_thread(_fit)

    return MlPrediction(
        symbol="",
        probability=float(prob),
        feature_importance=importance,
        model_version="lgbm_v1",
    )

# / ml-based signal generation using lightgbm
# / trains on indicator features, predicts forward return direction

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


# / phase 6 step 9: feature-set selector lets us a/b handbuilt vs alpha158 benchmark
# / values: "handbuilt" (default, ~13 features) | "alpha158" (~60 qlib features) | "both"
FEATURE_SET_HANDBUILT = "handbuilt"
FEATURE_SET_ALPHA158 = "alpha158"
FEATURE_SET_BOTH = "both"


def _feature_set() -> str:
    raw = os.environ.get("ML_FEATURE_SET", FEATURE_SET_HANDBUILT).strip().lower()
    if raw in {FEATURE_SET_HANDBUILT, FEATURE_SET_ALPHA158, FEATURE_SET_BOTH}:
        return raw
    logger.warning("ml_feature_set_unknown", raw=raw, falling_back=FEATURE_SET_HANDBUILT)
    return FEATURE_SET_HANDBUILT


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
    feature_set: str | None = None,
) -> pd.DataFrame:
    # / construct feature matrix from ohlcv + optional data
    # / feature_set: None (use ML_FEATURE_SET env) | "handbuilt" | "alpha158" | "both"
    fs = (feature_set or _feature_set()).lower()
    if fs not in {FEATURE_SET_HANDBUILT, FEATURE_SET_ALPHA158, FEATURE_SET_BOTH}:
        logger.warning("build_features_unknown_set", raw=fs, falling_back=FEATURE_SET_HANDBUILT)
        fs = FEATURE_SET_HANDBUILT
    df = ohlcv.copy()

    if fs in (FEATURE_SET_HANDBUILT, FEATURE_SET_BOTH):
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

    if fs in (FEATURE_SET_ALPHA158, FEATURE_SET_BOTH):
        # / phase 6 step 9: qlib-style feature bundle. cap to (5,10,20) windows
        # / on the first pass to keep training fast; expand after we have a brier baseline.
        from src.quant.alpha158 import compute_alpha158
        windows = (5, 10, 20)
        alpha = compute_alpha158(df[["open", "high", "low", "close", "volume"]], windows=windows)
        # / prefix alpha feature names so they don't collide with handbuilt ones
        alpha = alpha.rename(columns=lambda c: f"a158_{c}")
        df = df.join(alpha, how="left")

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
        importance = dict(zip(feature_cols, model.feature_importances_.tolist(), strict=False))
        top5 = dict(sorted(importance.items(), key=lambda x: x[1], reverse=True)[:5])
        return prob, top5

    prob, importance = await asyncio.to_thread(_fit)

    return MlPrediction(
        symbol="",
        probability=float(prob),
        feature_importance=importance,
        model_version="lgbm_v1",
    )


async def benchmark_feature_sets(
    ohlcv: pd.DataFrame,
    train_window: int = 252,
    forward_days: int = 5,
) -> dict:
    # / phase 6 step 9: train a lightgbm model on each feature set, return test brier
    # / so the dashboard can visibly compare handbuilt vs alpha158.
    # / the smaller brier wins (lower = better-calibrated probabilities).
    try:
        import lightgbm as lgb
    except ImportError:
        return {"error": "lightgbm_not_installed"}

    target = build_target(ohlcv, forward_days)

    def _score_for(feature_set: str) -> dict:
        features = build_features(ohlcv, feature_set=feature_set)
        common_idx = features.index.intersection(target.dropna().index)
        if len(common_idx) < train_window + forward_days + 30:
            return {"error": "insufficient_history", "rows": len(common_idx)}
        features = features.loc[common_idx]
        tgt = target.loc[common_idx]
        cols = [c for c in features.columns if c not in ["open", "high", "low", "close", "volume"]]
        # / walk-forward split: train on [-train_window-forward_days-30:-forward_days-30],
        # / test on the most-recent 30 rows we can still label.
        test_len = 30
        split = -(forward_days + test_len)
        X_train = features[cols].iloc[-(train_window + forward_days + test_len):split].to_numpy()
        y_train = tgt.iloc[-(train_window + forward_days + test_len):split].to_numpy()
        X_test = features[cols].iloc[split:-forward_days].to_numpy()
        y_test = tgt.iloc[split:-forward_days].to_numpy()
        if len(X_train) < 100 or len(X_test) < 10:
            return {"error": "insufficient_after_split", "train": len(X_train), "test": len(X_test)}
        model = lgb.LGBMClassifier(
            objective="binary", metric="binary_logloss",
            num_leaves=31, learning_rate=0.05, n_estimators=200,
            min_child_samples=20, subsample=0.8, colsample_bytree=0.8, verbose=-1,
        )
        model.fit(X_train, y_train)
        probs = model.predict_proba(X_test)[:, 1]
        brier = float(np.mean((probs - y_test) ** 2))
        # / information coefficient: spearman rank corr between prob and realized return
        # / use forward returns on the test window as the continuous target
        ic = None
        try:
            fwd_ret = ohlcv["close"].shift(-forward_days) / ohlcv["close"] - 1
            fwd_ret = fwd_ret.loc[features.index].iloc[split:-forward_days].to_numpy()
            if len(fwd_ret) == len(probs):
                r_prob = pd.Series(probs).rank().to_numpy()
                r_ret = pd.Series(fwd_ret).rank().to_numpy()
                denom = np.std(r_prob) * np.std(r_ret)
                if denom > 0:
                    ic = float(np.mean((r_prob - np.mean(r_prob)) * (r_ret - np.mean(r_ret))) / denom)
        except Exception:
            ic = None
        return {
            "brier": round(brier, 5),
            "ic": round(ic, 4) if ic is not None else None,
            "feature_count": len(cols),
            "train_rows": len(X_train),
            "test_rows": len(X_test),
        }

    def _run() -> dict:
        return {
            FEATURE_SET_HANDBUILT: _score_for(FEATURE_SET_HANDBUILT),
            FEATURE_SET_ALPHA158:  _score_for(FEATURE_SET_ALPHA158),
        }

    result = await asyncio.to_thread(_run)
    handbuilt = result.get(FEATURE_SET_HANDBUILT, {})
    alpha = result.get(FEATURE_SET_ALPHA158, {})
    winner = None
    if "brier" in handbuilt and "brier" in alpha:
        winner = FEATURE_SET_HANDBUILT if handbuilt["brier"] <= alpha["brier"] else FEATURE_SET_ALPHA158
    result["winner"] = winner
    return result


async def store_ml_prediction(pool, prediction: MlPrediction) -> None:
    # / persist ml prediction to db
    from datetime import date
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO ml_predictions (symbol, date, model_version, prediction, feature_importance)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (symbol, date) DO UPDATE SET
                    model_version = EXCLUDED.model_version,
                    prediction = EXCLUDED.prediction,
                    feature_importance = EXCLUDED.feature_importance""",
                prediction.symbol, date.today(), prediction.model_version,
                prediction.probability, json.dumps(prediction.feature_importance),
            )
    except Exception as exc:
        logger.warning("store_ml_prediction_failed", symbol=prediction.symbol, error=str(exc))

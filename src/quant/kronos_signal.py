# / kronos candle-sequence signal
# / phase 5 step 7: orthogonal signal source to lightgbm
# /
# / real kronos (shiyu-coder/Kronos, AAAI 2026) is a transformer that tokenizes
# / ohlcv candles and predicts the next-candle distribution. ~100m-1b params.
# /
# / this module exposes a predict() interface that:
# /   - uses the real HF model if KRONOS_ENABLED=true AND transformers is available
# /   - otherwise returns a statistical-baseline probability (honest fallback, not
# /     pretending to be the neural model)
# /
# / the fallback is a weighted combination of momentum, volatility compression,
# / and short-term mean reversion — useful on its own and a reasonable floor
# / while the real model is being set up on the vps.

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

SignalSource = Literal["kronos_hf", "fallback_heuristic", "insufficient_data"]


@dataclass
class KronosPrediction:
    """output of kronos signal inference

    probability is the probability the next close will be higher than the
    most recent close. in [0,1]. 0.5 = no edge.
    """
    symbol: str
    probability: float
    confidence: float  # / 0..1, higher when model more certain
    source: SignalSource
    lookback: int
    model_version: str | None = None
    components: dict | None = None  # / breakdown (for fallback) or attention (for hf)


# / module-level model handle; lazy-loaded
_model = None
_tokenizer = None
_model_load_attempted = False
_model_load_failed_reason: str | None = None


def _available_memory_mb() -> float | None:
    # / used by the memory guard — returns None when psutil is unavailable
    try:
        import psutil  # type: ignore
    except ImportError:
        return None
    try:
        return psutil.virtual_memory().available / (1024 * 1024)
    except Exception:
        return None


KRONOS_MIN_MEMORY_MB = 1500  # / skip load if the box has less than ~1.5 GB free

# / NeoQuasar is the canonical HF org for Kronos (the shiyu-coder github repo publishes
# / weights to NeoQuasar/Kronos-*). both the tokenizer and model use trust_remote_code
# / so the custom `Kronos`, `KronosTokenizer`, `KronosPredictor` classes get pulled in.
# / defaults: Kronos-small (25M params) balances quality with 8GB vps memory budget.
DEFAULT_TOKENIZER_ID = "NeoQuasar/Kronos-Tokenizer-base"
DEFAULT_MODEL_ID = "NeoQuasar/Kronos-small"

_predictor = None  # / KronosPredictor instance after successful load


def _try_load_hf_model() -> bool:
    # / attempt to lazy-load the real Kronos HF model. returns True on success.
    # / caches result so we don't retry on every call.
    global _model, _tokenizer, _predictor, _model_load_attempted, _model_load_failed_reason
    if _model_load_attempted:
        return _predictor is not None

    _model_load_attempted = True

    if os.environ.get("KRONOS_ENABLED", "false").lower() not in ("true", "1", "yes"):
        _model_load_failed_reason = "disabled_via_env"
        return False

    # / memory guard — refuse to load on a box that's already under pressure.
    # / a single-cycle skip is far better than taking the orchestrator down.
    avail_mb = _available_memory_mb()
    if avail_mb is not None and avail_mb < KRONOS_MIN_MEMORY_MB:
        _model_load_failed_reason = f"memory_guard_triggered: {avail_mb:.0f}MB < {KRONOS_MIN_MEMORY_MB}MB"
        logger.warning("kronos_mem_guard_triggered", available_mb=round(avail_mb))
        # / don't latch — let a future call retry once memory frees up
        _model_load_attempted = False
        return False

    try:
        # / these imports depend on trust_remote_code pulling the Kronos classes from HF
        from transformers import AutoModel, AutoTokenizer  # type: ignore  # noqa: F401
    except ImportError:
        _model_load_failed_reason = "transformers_not_installed"
        logger.info("kronos_transformers_missing_using_fallback")
        return False

    tokenizer_id = os.environ.get("KRONOS_TOKENIZER_ID", DEFAULT_TOKENIZER_ID)
    model_id = os.environ.get("KRONOS_MODEL_ID", DEFAULT_MODEL_ID)
    device = os.environ.get("KRONOS_DEVICE", "cpu")
    max_context = int(os.environ.get("KRONOS_MAX_CONTEXT", "512"))
    try:
        # / the `Kronos`, `KronosTokenizer`, `KronosPredictor` classes live in the HF
        # / repo's modeling code. loading with trust_remote_code=True registers them
        # / under their real names; AutoModel.from_pretrained picks the custom class.
        _tokenizer = AutoModel.from_pretrained(tokenizer_id, trust_remote_code=True)
        _model = AutoModel.from_pretrained(model_id, trust_remote_code=True)
        # / KronosPredictor is a custom wrapper class that ships with the model. we
        # / look it up on the loaded module rather than importing a top-level name
        # / so we don't have to care which file the HF repo puts it in.
        predictor_cls = None
        for mod in (_model.__class__.__module__, _tokenizer.__class__.__module__):
            try:
                import importlib
                m = importlib.import_module(mod)
                predictor_cls = getattr(m, "KronosPredictor", None)
                if predictor_cls is not None:
                    break
            except Exception:
                continue
        if predictor_cls is None:
            raise RuntimeError("KronosPredictor class not found in loaded module")
        _predictor = predictor_cls(_model, _tokenizer, device=device, max_context=max_context)
        logger.info("kronos_hf_model_loaded", model_id=model_id, tokenizer_id=tokenizer_id, device=device)
        return True
    except Exception as exc:
        _model_load_failed_reason = f"load_error: {str(exc)[:200]}"
        logger.warning("kronos_hf_load_failed_using_fallback", error=str(exc)[:300])
        _model = None
        _tokenizer = None
        _predictor = None
        return False


def _fallback_heuristic(ohlcv: pd.DataFrame, lookback: int) -> tuple[float, float, dict]:
    # / statistical baseline — honest fallback, not pretending to be kronos.
    # /
    # / returns (prob_up, confidence, components)
    # /
    # / combines three orthogonal signals, each in [-1, 1], then sigmoid to [0,1]:
    # /   momentum: normalized 20-day return (winsorized)
    # /   vol_compression: current vol < median vol suggests breakout coming
    # /   short_term_reversion: extreme 1-day moves tend to mean-revert
    window = ohlcv.tail(lookback).copy()
    if len(window) < 20:
        return 0.5, 0.0, {"reason": "insufficient_data", "bars": len(window)}

    closes = window["close"].to_numpy()
    returns = np.diff(closes) / closes[:-1]

    # / momentum: normalized 20-day return, winsorized at +/-20%
    lookback_n = min(20, len(returns))
    momentum_raw = (closes[-1] / closes[-lookback_n - 1] - 1) if len(closes) > lookback_n else 0.0
    momentum = float(np.clip(momentum_raw / 0.20, -1.0, 1.0))

    # / vol compression: current 5d realized vol vs 20d median
    if len(returns) >= 20:
        vol_recent = float(np.std(returns[-5:])) if len(returns) >= 5 else 0.0
        vol_baseline = float(np.median(np.abs(returns[-20:]))) + 1e-9
        compression = float(np.clip((vol_baseline - vol_recent) / vol_baseline, -1.0, 1.0))
    else:
        compression = 0.0

    # / short-term mean reversion: if last day was an extreme move, expect reversion
    if len(returns) >= 5:
        last_return = float(returns[-1])
        vol_20d = float(np.std(returns[-20:])) if len(returns) >= 20 else float(np.std(returns))
        if vol_20d > 0:
            z_score = last_return / vol_20d
            reversion = float(np.clip(-z_score / 3.0, -1.0, 1.0))
        else:
            reversion = 0.0
    else:
        reversion = 0.0

    # / weighted combine — weights chosen so each component has similar influence
    combined = 0.5 * momentum + 0.3 * compression + 0.2 * reversion

    # / sigmoid to probability
    prob_up = 1.0 / (1.0 + np.exp(-2.0 * combined))

    # / confidence: how extreme is the combined signal? |combined| -> higher confidence
    confidence = float(min(abs(combined), 1.0))

    components = {
        "momentum": round(momentum, 4),
        "vol_compression": round(compression, 4),
        "reversion": round(reversion, 4),
        "combined": round(float(combined), 4),
    }
    return float(prob_up), confidence, components


def _run_hf_inference(
    ohlcv: pd.DataFrame, lookback: int
) -> tuple[float, float, dict] | None:
    # / runs real Kronos inference via the KronosPredictor API. Returns None on
    # / failure so the caller can gracefully fall back.
    # /
    # / Kronos's predictor takes an OHLCV dataframe + timestamps and returns point
    # / forecasts. to get a probability, we run sample_count paths and count how
    # / many end with next_close > last_actual_close.
    global _predictor
    if _predictor is None:
        return None

    try:
        window = ohlcv.tail(lookback).copy()
        if len(window) < 32:
            return None

        # / Kronos expects pandas DataFrame with columns open/high/low/close,
        # / optionally volume + amount. we have ohlcv; skip `amount`.
        df_in = window[["open", "high", "low", "close", "volume"]].astype(float).reset_index(drop=True)

        # / synthetic daily timestamps — only the cadence matters to the positional
        # / embedding. real timestamps aren't exposed here so we fabricate them.
        x_timestamp = pd.Series(pd.date_range(end=pd.Timestamp.utcnow().normalize(), periods=len(df_in), freq="D"))
        y_timestamp = pd.Series(pd.date_range(start=x_timestamp.iloc[-1] + pd.Timedelta(days=1), periods=1, freq="D"))

        sample_count = int(os.environ.get("KRONOS_SAMPLE_COUNT", "30"))
        pred_df = _predictor.predict(
            df=df_in,
            x_timestamp=x_timestamp,
            y_timestamp=y_timestamp,
            pred_len=1,
            T=1.0,
            top_p=0.9,
            sample_count=sample_count,
        )

        # / sample paths can come back as stacked rows (n_samples rows) OR as a
        # / single-row df of averages depending on KronosPredictor version. handle both.
        last_close = float(df_in["close"].iloc[-1])
        if hasattr(pred_df, "shape") and len(pred_df) >= 2:
            predicted_closes = pred_df["close"].to_numpy().astype(float)
            prob_up = float(np.mean(predicted_closes > last_close))
        else:
            # / fall-back: single forecast → deterministic direction, confidence shrinks
            pred_close = float(pred_df["close"].iloc[0])
            prob_up = 1.0 if pred_close > last_close else 0.0 if pred_close < last_close else 0.5
            sample_count = 1

        # / confidence scales with sample disagreement — tight consensus = high confidence
        confidence = float(min(abs(prob_up - 0.5) * 2.0, 1.0))
        components = {
            "source": "kronos_hf_predict",
            "sample_count": sample_count,
            "last_close": round(last_close, 4),
        }
        return prob_up, confidence, components
    except Exception as exc:
        logger.warning("kronos_hf_inference_failed", error=str(exc)[:200])
        return None


def predict(symbol: str, ohlcv: pd.DataFrame, lookback: int = 64) -> KronosPrediction:
    """predict probability the next close is higher than the last close.

    args:
        symbol: the asset symbol (for logging only)
        ohlcv: dataframe with columns open/high/low/close/volume, sorted old->new
        lookback: number of bars to feed the model. default 64 (~3 months daily)

    returns:
        KronosPrediction with probability in [0,1] and source tag.

    falls back to a statistical heuristic if the HF model is disabled or unavailable.
    always returns a prediction — never raises. insufficient data yields prob=0.5.
    """
    if ohlcv is None or len(ohlcv) == 0:
        return KronosPrediction(
            symbol=symbol, probability=0.5, confidence=0.0,
            source="insufficient_data", lookback=lookback,
        )

    required_cols = {"open", "high", "low", "close", "volume"}
    if not required_cols.issubset(ohlcv.columns):
        logger.warning("kronos_missing_columns", symbol=symbol, have=list(ohlcv.columns))
        return KronosPrediction(
            symbol=symbol, probability=0.5, confidence=0.0,
            source="insufficient_data", lookback=lookback,
        )

    # / try real model first if enabled + available
    if _try_load_hf_model():
        result = _run_hf_inference(ohlcv, lookback)
        if result is not None:
            prob, conf, components = result
            return KronosPrediction(
                symbol=symbol, probability=prob, confidence=conf,
                source="kronos_hf", lookback=lookback,
                model_version=os.environ.get("KRONOS_MODEL_ID", DEFAULT_MODEL_ID),
                components=components,
            )

    # / fallback: statistical heuristic
    prob, conf, components = _fallback_heuristic(ohlcv, lookback)
    return KronosPrediction(
        symbol=symbol, probability=prob, confidence=conf,
        source="fallback_heuristic", lookback=lookback,
        model_version=None, components=components,
    )


def is_hf_available() -> bool:
    """reports whether the real HF model is loaded and ready.

    useful for dashboard health tiles — if False, predictions are using the
    fallback heuristic only.
    """
    _try_load_hf_model()
    return _predictor is not None


def get_load_status() -> dict:
    """returns a small dict describing the real-model load state.

    used by the dashboard Health tab to surface "kronos is falling back" as a
    visible signal rather than a silent degradation.
    """
    return {
        "hf_loaded": _predictor is not None,
        "load_attempted": _model_load_attempted,
        "fallback_reason": _model_load_failed_reason,
        "enabled_via_env": os.environ.get("KRONOS_ENABLED", "false").lower() in ("true", "1", "yes"),
    }

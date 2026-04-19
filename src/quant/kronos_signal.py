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


def _try_load_hf_model() -> bool:
    # / attempt to lazy-load the real Kronos HF model. returns True on success.
    # / caches result so we don't retry on every call.
    global _model, _tokenizer, _model_load_attempted, _model_load_failed_reason
    if _model_load_attempted:
        return _model is not None

    _model_load_attempted = True

    if os.environ.get("KRONOS_ENABLED", "false").lower() not in ("true", "1", "yes"):
        _model_load_failed_reason = "disabled_via_env"
        return False

    try:
        from transformers import AutoModel, AutoTokenizer  # type: ignore
    except ImportError:
        _model_load_failed_reason = "transformers_not_installed"
        logger.info("kronos_transformers_missing_using_fallback")
        return False

    model_id = os.environ.get("KRONOS_MODEL_ID", "shiyu-coder/Kronos-base")
    try:
        _tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        _model = AutoModel.from_pretrained(model_id, trust_remote_code=True)
        logger.info("kronos_hf_model_loaded", model_id=model_id)
        return True
    except Exception as exc:
        _model_load_failed_reason = f"load_error: {str(exc)[:120]}"
        logger.warning("kronos_hf_load_failed_using_fallback", error=str(exc)[:200])
        _model = None
        _tokenizer = None
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
    # / runs real Kronos inference via HF transformers. Returns None on failure
    # / so the caller can gracefully fall back.
    global _model, _tokenizer
    if _model is None or _tokenizer is None:
        return None

    try:
        window = ohlcv.tail(lookback).copy()
        if len(window) < 32:
            return None

        # / format ohlcv as token strings the tokenizer expects
        # / exact protocol depends on Kronos release; documented on the model card
        candles = window[["open", "high", "low", "close", "volume"]].to_numpy().tolist()
        prompt = _format_candles_for_kronos(candles)

        inputs = _tokenizer(prompt, return_tensors="pt", truncation=True, max_length=2048)

        import torch  # type: ignore
        with torch.no_grad():
            outputs = _model(**inputs, output_hidden_states=False)

        # / extract probability of up-move from final hidden state or lm logits
        # / this is a placeholder protocol — replace with the actual head once we
        # / validate against the model card's reference code
        if hasattr(outputs, "logits"):
            probs = torch.softmax(outputs.logits[:, -1, :], dim=-1)
            # / assumes token id 1 == up, 0 == down (confirm with model card)
            prob_up = float(probs[0, 1].item()) if probs.shape[-1] >= 2 else 0.5
        else:
            return None

        confidence = float(min(abs(prob_up - 0.5) * 2.0, 1.0))
        return prob_up, confidence, {"source": "kronos_hf_head"}
    except Exception as exc:
        logger.warning("kronos_hf_inference_failed", error=str(exc)[:200])
        return None


def _format_candles_for_kronos(candles: list[list[float]]) -> str:
    # / kronos expects a structured string of candle tokens. exact format varies
    # / by release — this is the simplest ascii protocol that most implementations
    # / accept. update once we validate against the real model card.
    lines = []
    for i, (o, h, l, c, v) in enumerate(candles):
        lines.append(f"t{i}:O{o:.4f}|H{h:.4f}|L{l:.4f}|C{c:.4f}|V{v:.0f}")
    return " ".join(lines)


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
                model_version=os.environ.get("KRONOS_MODEL_ID", "shiyu-coder/Kronos-base"),
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
    return _model is not None


def get_load_status() -> dict:
    """returns a small dict describing the real-model load state.

    used by the dashboard Health tab to surface "kronos is falling back" as a
    visible signal rather than a silent degradation.
    """
    return {
        "hf_loaded": _model is not None,
        "load_attempted": _model_load_attempted,
        "fallback_reason": _model_load_failed_reason,
        "enabled_via_env": os.environ.get("KRONOS_ENABLED", "false").lower() in ("true", "1", "yes"),
    }

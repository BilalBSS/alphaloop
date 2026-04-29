# / chart indicator dispatch table

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd
import structlog

from src.indicators import momentum, support_resistance, trend, volatility, volume

logger = structlog.get_logger(__name__)


@dataclass
class IndicatorSpec:
    pane: str  # / "price" | "rsi" |
    compute: Callable[[pd.DataFrame], dict]


def _to_list(s: pd.Series) -> list:
    out: list = []
    for v in s.tolist():
        if v is None or (isinstance(v, float) and math.isnan(v)):
            out.append(None)
        else:
            out.append(float(v))
    return out


def _series(pane: str, values: pd.Series) -> dict:
    # / single-value series result
    return {"kind": "series", "pane": pane, "values": _to_list(values)}


def _bb(df: pd.DataFrame, period: int, std: float) -> dict:
    # / bollinger bands
    bb = volatility.bollinger_bands(df["close"], period, std)
    return {
        "kind": "series",
        "pane": "price",
        "upper": _to_list(bb.upper),
        "middle": _to_list(bb.middle),
        "lower": _to_list(bb.lower),
    }


def _keltner(df: pd.DataFrame, ema_p: int, atr_p: int, mult: float) -> dict:
    # / keltner channel
    kc = volatility.keltner_channel(df["high"], df["low"], df["close"], ema_p, atr_p, mult)
    return {
        "kind": "series",
        "pane": "price",
        "upper": _to_list(kc.upper),
        "middle": _to_list(kc.middle),
        "lower": _to_list(kc.lower),
    }


def _ichimoku(df: pd.DataFrame, c: int, b: int, s: int, d: int) -> dict:
    # / ichimoku cloud
    ich = trend.ichimoku(df["high"], df["low"], df["close"], c, b, s, d)
    return {
        "kind": "series",
        "pane": "price",
        "conversion": _to_list(ich.conversion),
        "base": _to_list(ich.base),
        "span_a": _to_list(ich.span_a),
        "span_b": _to_list(ich.span_b),
        "lagging": _to_list(ich.lagging),
    }


def _psar(df: pd.DataFrame, step: float, maxs: float) -> dict:
    # / parabolic sar
    res = trend.psar(df["high"], df["low"], step, maxs)
    return {
        "kind": "series",
        "pane": "price",
        "sar": _to_list(res.sar),
        "direction": _to_list(res.direction),
    }


def _supertrend(df: pd.DataFrame, period: int, mult: float) -> dict:
    # / supertrend line + direction
    res = trend.supertrend(df["high"], df["low"], df["close"], period, mult)
    return {
        "kind": "series",
        "pane": "price",
        "line": _to_list(res.supertrend),
        "direction": _to_list(res.direction.astype(float)),
    }


def _donchian(df: pd.DataFrame, period: int) -> dict:
    # / donchian channel
    res = trend.donchian_channel(df["high"], df["low"], period)
    return {
        "kind": "series",
        "pane": "price",
        "upper": _to_list(res.upper),
        "middle": _to_list(res.middle),
        "lower": _to_list(res.lower),
    }


def _fib_auto(df: pd.DataFrame, lookback: int) -> dict:
    fib = support_resistance.fib_auto(df["high"], df["low"], df["close"], lookback)
    return {
        "kind": "horizontal_levels",
        "pane": "price",
        "levels": {
            "level_236": float(fib.level_236),
            "level_382": float(fib.level_382),
            "level_500": float(fib.level_500),
            "level_618": float(fib.level_618),
            "level_786": float(fib.level_786),
        },
        "swing_high": float(fib.swing_high),
        "swing_low": float(fib.swing_low),
    }


def _macd(df: pd.DataFrame, fast: int, slow: int, signal: int) -> dict:
    res = trend.macd(df["close"], fast, slow, signal)
    return {
        "kind": "series",
        "pane": "macd",
        "line": _to_list(res.macd_line),
        "signal": _to_list(res.signal_line),
        "hist": _to_list(res.histogram),
    }


def _stoch(df: pd.DataFrame, k: int, d: int) -> dict:
    # / stochastic %k + %d
    res = momentum.stochastic(df["high"], df["low"], df["close"], k, d)
    return {
        "kind": "series",
        "pane": "stoch",
        "k": _to_list(res.k),
        "d": _to_list(res.d),
    }


REGISTRY: dict[str, IndicatorSpec] = {
    "sma_20": IndicatorSpec("price", lambda df: _series("price", trend.sma(df["close"], 20))),
    "sma_50": IndicatorSpec("price", lambda df: _series("price", trend.sma(df["close"], 50))),
    "sma_200": IndicatorSpec("price", lambda df: _series("price", trend.sma(df["close"], 200))),
    "ema_20": IndicatorSpec("price", lambda df: _series("price", trend.ema(df["close"], 20))),
    "ema_50": IndicatorSpec("price", lambda df: _series("price", trend.ema(df["close"], 50))),
    "ema_200": IndicatorSpec("price", lambda df: _series("price", trend.ema(df["close"], 200))),
    # / bollinger 20/2
    "bb_20_2": IndicatorSpec("price", lambda df: _bb(df, 20, 2.0)),
    # / keltner 20/10/2
    "keltner_20_10_2": IndicatorSpec("price", lambda df: _keltner(df, 20, 10, 2.0)),
    # / vwap
    "vwap": IndicatorSpec("price", lambda df: _series("price", volume.vwap(df["high"], df["low"], df["close"], df["volume"]))),
    # / ichimoku 9/26/52/26
    "ichimoku_9_26_52_26": IndicatorSpec("price", lambda df: _ichimoku(df, 9, 26, 52, 26)),
    "psar_2_20": IndicatorSpec("price", lambda df: _psar(df, 0.02, 0.2)),
    # / supertrend 10/3
    "supertrend_10_3": IndicatorSpec("price", lambda df: _supertrend(df, 10, 3.0)),
    # / donchian 20
    "donchian_20": IndicatorSpec("price", lambda df: _donchian(df, 20)),
    # / fib auto 100
    "fib_auto_100": IndicatorSpec("price", lambda df: _fib_auto(df, 100)),
    # / oscillator panes
    "rsi_14": IndicatorSpec("rsi", lambda df: _series("rsi", momentum.rsi(df["close"], 14))),
    "macd_12_26_9": IndicatorSpec("macd", lambda df: _macd(df, 12, 26, 9)),
    "stoch_14_3_3": IndicatorSpec("stoch", lambda df: _stoch(df, 14, 3)),
    "adx_14": IndicatorSpec("adx", lambda df: _series("adx", trend.adx(df["high"], df["low"], df["close"], 14))),
    "cci_20": IndicatorSpec("cci", lambda df: _series("cci", momentum.cci(df["high"], df["low"], df["close"], 20))),
    "williams_14": IndicatorSpec("williams", lambda df: _series("williams", momentum.williams_r(df["high"], df["low"], df["close"], 14))),
    "obv": IndicatorSpec("obv", lambda df: _series("obv", volume.obv(df["close"], df["volume"]))),
    "mfi_14": IndicatorSpec("mfi", lambda df: _series("mfi", volume.mfi(df["high"], df["low"], df["close"], df["volume"], 14))),
    "atr_14": IndicatorSpec("atr", lambda df: _series("atr", volatility.atr(df["high"], df["low"], df["close"], 14))),
    "roc_12": IndicatorSpec("roc", lambda df: _series("roc", momentum.roc(df["close"], 12))),
}


def compute(df: pd.DataFrame, indicator_id: str) -> dict | None:
    spec = REGISTRY.get(indicator_id)
    if spec is None:
        return None
    try:
        return spec.compute(df)
    except Exception as exc:
        logger.debug("indicator_compute_failed", indicator=indicator_id, exc_type=type(exc).__name__)
        return None


def available_indicators() -> list[str]:
    return sorted(REGISTRY.keys())

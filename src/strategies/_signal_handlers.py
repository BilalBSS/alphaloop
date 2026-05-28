from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

SIGNAL_HANDLERS: dict[str, Callable[[dict[str, Any], pd.DataFrame], tuple[bool, float, str]]] = {}


def register_signal(name: str):
    def decorator(fn):
        SIGNAL_HANDLERS[name] = fn
        return fn
    return decorator


@register_signal("bollinger_bands")
def _eval_bollinger(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    from src.indicators.volatility import bollinger_bands
    close = market_data["close"]
    condition = sig.get("condition", "")
    period = sig.get("lookback", sig.get("period", 20))
    std = sig.get("std_dev", 2.0)
    bb = bollinger_bands(close, period=period, std_dev=std)
    last_close = float(close.iloc[-1])
    if condition == "price_below_lower":
        last_lower = float(bb.lower.iloc[-1])
        passed = last_close < last_lower
        strength = max(0, min(1, (last_lower - last_close) / (last_lower * 0.01 + 1e-9)))
        return passed, strength if passed else 0.0, f"bb: close={last_close:.2f} {'<' if passed else '>='} lower={last_lower:.2f}"
    elif condition == "price_above_upper":
        last_upper = float(bb.upper.iloc[-1])
        passed = last_close > last_upper
        strength = max(0, min(1, (last_close - last_upper) / (last_upper * 0.01 + 1e-9)))
        return passed, strength if passed else 0.0, f"bb: close={last_close:.2f} {'>' if passed else '<='} upper={last_upper:.2f}"
    elif condition == "price_above_middle":
        last_mid = float(bb.middle.iloc[-1])
        passed = last_close > last_mid
        return passed, 0.5 if passed else 0.0, f"bb: close={last_close:.2f} {'>' if passed else '<='} middle={last_mid:.2f}"
    elif condition == "price_below_upper":
        last_upper = float(bb.upper.iloc[-1])
        passed = last_close < last_upper
        return passed, 0.5 if passed else 0.0, f"bb: close={last_close:.2f} {'<' if passed else '>='} upper={last_upper:.2f}"
    return False, 0.0, f"unknown bb condition: {condition}"


@register_signal("rsi")
def _eval_rsi(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    from src.indicators.momentum import rsi as rsi_fn
    close = market_data["close"]
    condition = sig.get("condition", "")
    period = int(sig.get("period") or 14)
    threshold = float(sig.get("threshold") or 30)
    rsi_val = rsi_fn(close, period=period)
    last_rsi = float(rsi_val.dropna().iloc[-1]) if not rsi_val.dropna().empty else 50.0
    if condition == "below":
        passed = last_rsi < threshold
        strength = max(0, min(1, (threshold - last_rsi) / threshold)) if passed else 0.0
        return passed, strength, f"rsi={last_rsi:.1f} {'<' if passed else '>='} {threshold}"
    elif condition == "above":
        passed = last_rsi > threshold
        strength = max(0, min(1, (last_rsi - threshold) / (100 - threshold))) if passed else 0.0
        return passed, strength, f"rsi={last_rsi:.1f} {'>' if passed else '<='} {threshold}"
    elif condition == "between":
        low = float(sig.get("low") or 40)
        high = float(sig.get("high") or 60)
        passed = low <= last_rsi <= high
        return passed, 0.5 if passed else 0.0, f"rsi={last_rsi:.1f} {'in' if passed else 'outside'} [{low}, {high}]"
    return False, 0.0, f"unknown rsi condition: {condition}"


@register_signal("macd")
def _eval_macd(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    from src.indicators.trend import macd as macd_fn
    close = market_data["close"]
    condition = sig.get("condition", "")
    lookback = max(1, int(sig.get("lookback") or 3))
    hist_series = macd_fn(close).histogram.dropna()
    if len(hist_series) < 2:
        return False, 0.0, "macd: insufficient history"
    hist = hist_series.tolist()
    last_hist = float(hist[-1])
    window = hist[-(lookback + 1):]
    if condition in ("crossover_bullish", "crossover_bullish_within"):
        crossed = any(window[i] < 0 and window[i + 1] >= 0 for i in range(len(window) - 1))
        return crossed, 0.7 if crossed else 0.0, (
            f"macd crossover_bullish in last {lookback} bars (last={last_hist:.4f})"
        )
    elif condition in ("crossover_bearish", "crossover_bearish_within"):
        crossed = any(window[i] > 0 and window[i + 1] <= 0 for i in range(len(window) - 1))
        return crossed, 0.7 if crossed else 0.0, (
            f"macd crossover_bearish in last {lookback} bars (last={last_hist:.4f})"
        )
    elif condition == "positive":
        passed = last_hist > 0
        return passed, 0.5 if passed else 0.0, f"macd histogram={last_hist:.4f}"
    return False, 0.0, f"unknown macd condition: {condition}"


@register_signal("volume")
def _eval_volume(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    volume = market_data["volume"]
    condition = sig.get("condition", "")
    period = sig.get("period", 20)
    multiplier = sig.get("multiplier", 1.5)
    avg_vol = float(volume.rolling(window=period, min_periods=period).mean().iloc[-1])
    last_vol = float(volume.iloc[-1])
    if condition == "above_average":
        passed = last_vol > avg_vol * multiplier
        strength = min(1, last_vol / (avg_vol * multiplier)) if avg_vol > 0 else 0.0
        return passed, strength if passed else 0.0, f"vol={last_vol:.0f} {'>' if passed else '<='} {multiplier}x avg={avg_vol:.0f}"
    return False, 0.0, f"unknown volume condition: {condition}"


@register_signal("sma")
def _eval_sma(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    from src.indicators.trend import sma as sma_fn
    close = market_data["close"]
    condition = sig.get("condition", "")
    if condition in ("above", "golden_cross", "fast_above_slow"):
        fast = int(sig.get("fast_period") or 50)
        slow = int(sig.get("slow_period") or 200)
        fast_sma = sma_fn(close, period=fast).dropna()
        slow_sma = sma_fn(close, period=slow).dropna()
        if fast_sma.empty or slow_sma.empty:
            return False, 0.0, f"sma {fast}/{slow}: insufficient history"
        passed = float(fast_sma.iloc[-1]) > float(slow_sma.iloc[-1])
        return passed, 0.5 if passed else 0.0, f"sma{fast}={fast_sma.iloc[-1]:.2f} {'>' if passed else '<='} sma{slow}={slow_sma.iloc[-1]:.2f}"
    if condition in ("below", "death_cross", "fast_below_slow"):
        fast = int(sig.get("fast_period") or 50)
        slow = int(sig.get("slow_period") or 200)
        fast_sma = sma_fn(close, period=fast).dropna()
        slow_sma = sma_fn(close, period=slow).dropna()
        if fast_sma.empty or slow_sma.empty:
            return False, 0.0, f"sma {fast}/{slow}: insufficient history"
        passed = float(fast_sma.iloc[-1]) < float(slow_sma.iloc[-1])
        return passed, 0.5 if passed else 0.0, f"sma{fast}={fast_sma.iloc[-1]:.2f} {'<' if passed else '>='} sma{slow}={slow_sma.iloc[-1]:.2f}"
    period = int(sig.get("period") or 50)
    sma_val = sma_fn(close, period=period)
    last_close = float(close.iloc[-1])
    last_sma = float(sma_val.iloc[-1])
    if condition == "price_above":
        passed = last_close > last_sma
        return passed, 0.5 if passed else 0.0, f"close={last_close:.2f} {'>' if passed else '<='} sma{period}={last_sma:.2f}"
    elif condition == "price_below":
        passed = last_close < last_sma
        return passed, 0.5 if passed else 0.0, f"close={last_close:.2f} {'<' if passed else '>='} sma{period}={last_sma:.2f}"
    return False, 0.0, f"unknown sma condition: {condition}"


@register_signal("adx")
def _eval_adx(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    from src.indicators.trend import adx as adx_fn
    close = market_data["close"]
    high = market_data["high"]
    low = market_data["low"]
    condition = sig.get("condition", "")
    period = sig.get("period", 14)
    threshold = sig.get("threshold", 25)
    adx_val = adx_fn(high, low, close, period=period)
    last_adx = float(adx_val.dropna().iloc[-1]) if not adx_val.dropna().empty else 0.0
    if condition == "above":
        passed = last_adx > threshold
        return passed, min(1, last_adx / 50) if passed else 0.0, f"adx={last_adx:.1f} {'>' if passed else '<='} {threshold}"
    elif condition == "below":
        passed = last_adx < threshold
        return passed, 0.5 if passed else 0.0, f"adx={last_adx:.1f} {'<' if passed else '>='} {threshold}"
    return False, 0.0, f"unknown adx condition: {condition}"


@register_signal("atr")
def _eval_atr(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    from src.indicators.volatility import atr as atr_fn
    close = market_data["close"]
    high = market_data["high"]
    low = market_data["low"]
    condition = sig.get("condition", "")
    period = sig.get("period", 14)
    threshold = sig.get("threshold", 0)
    atr_val = atr_fn(high, low, close, period=period)
    last_atr = float(atr_val.dropna().iloc[-1]) if not atr_val.dropna().empty else 0.0
    last_close = float(close.iloc[-1])
    atr_pct = last_atr / last_close if last_close > 0 else 0
    if condition == "above":
        passed = atr_pct > threshold
        return passed, 0.5 if passed else 0.0, f"atr%={atr_pct:.4f} {'>' if passed else '<='} {threshold}"
    elif condition == "below":
        passed = atr_pct < threshold
        return passed, 0.5 if passed else 0.0, f"atr%={atr_pct:.4f} {'<' if passed else '>='} {threshold}"
    return True, 0.5, f"atr={last_atr:.2f} ({atr_pct:.2%} of price)"


@register_signal("stochastic")
def _eval_stochastic(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    from src.indicators.momentum import stochastic as stoch_fn
    close = market_data["close"]
    high = market_data["high"]
    low = market_data["low"]
    condition = sig.get("condition", "")
    period = sig.get("period", 14)
    threshold = sig.get("threshold", 20)
    result = stoch_fn(high, low, close, k_period=period)
    last_k = float(result.k.dropna().iloc[-1]) if not result.k.dropna().empty else 50.0
    if condition == "below":
        passed = last_k < threshold
        return passed, max(0, min(1, (threshold - last_k) / threshold)) if passed else 0.0, f"stoch %k={last_k:.1f} {'<' if passed else '>='} {threshold}"
    elif condition == "above":
        passed = last_k > threshold
        return passed, max(0, min(1, (last_k - threshold) / (100 - threshold))) if passed else 0.0, f"stoch %k={last_k:.1f} {'>' if passed else '<='} {threshold}"
    return False, 0.0, f"unknown stochastic condition: {condition}"


@register_signal("fair_value_gap")
def _eval_fvg(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    from src.indicators.structure import fair_value_gaps
    close = market_data["close"]
    high = market_data["high"]
    low = market_data["low"]
    condition = sig.get("condition", "")
    result = fair_value_gaps(high, low, close)
    last_sig = int(result.signal.iloc[-1])
    if condition == "bullish":
        passed = last_sig == 1
        return passed, 0.7 if passed else 0.0, f"fvg signal={last_sig}"
    elif condition == "bearish":
        passed = last_sig == -1
        return passed, 0.7 if passed else 0.0, f"fvg signal={last_sig}"
    passed = last_sig != 0
    return passed, 0.6 if passed else 0.0, f"fvg signal={last_sig}"


@register_signal("order_block")
def _eval_order_block(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    from src.indicators.structure import order_blocks
    close = market_data["close"]
    high = market_data["high"]
    low = market_data["low"]
    condition = sig.get("condition", "")
    open_ = market_data["open"]
    result = order_blocks(high, low, close, open_)
    last_sig = int(result.signal.iloc[-1])
    if condition == "bullish":
        passed = last_sig == 1
    elif condition == "bearish":
        passed = last_sig == -1
    else:
        passed = last_sig != 0
    return passed, 0.7 if passed else 0.0, f"ob signal={last_sig}"


@register_signal("structure_break")
def _eval_structure_break(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    from src.indicators.structure import structure_breaks
    close = market_data["close"]
    high = market_data["high"]
    low = market_data["low"]
    condition = sig.get("condition", "")
    lookback = sig.get("lookback", 5)
    result = structure_breaks(high, low, close, swing_lookback=lookback)
    last_sig = int(result.signal.iloc[-1])
    if condition == "bullish":
        passed = last_sig == 1
    elif condition == "bearish":
        passed = last_sig == -1
    else:
        passed = last_sig != 0
    return passed, 0.8 if passed else 0.0, f"structure break={last_sig}"


@register_signal("pivot_points")
def _eval_pivot_points(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    from src.indicators.support_resistance import pivot_points
    close = market_data["close"]
    high = market_data["high"]
    low = market_data["low"]
    condition = sig.get("condition", "")
    pp = pivot_points(float(high.iloc[-2]), float(low.iloc[-2]), float(close.iloc[-2]))
    last_close = float(close.iloc[-1])
    if condition == "above_r1":
        passed = last_close > pp.r1
        return passed, 0.6 if passed else 0.0, f"close={last_close:.2f} vs r1={pp.r1:.2f}"
    elif condition == "below_s1":
        passed = last_close < pp.s1
        return passed, 0.6 if passed else 0.0, f"close={last_close:.2f} vs s1={pp.s1:.2f}"
    passed = last_close < pp.pivot
    return passed, 0.5 if passed else 0.0, f"close={last_close:.2f} vs pivot={pp.pivot:.2f}"


@register_signal("fibonacci")
def _eval_fibonacci(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    from src.indicators.support_resistance import fibonacci_retracement
    close = market_data["close"]
    high = market_data["high"]
    low = market_data["low"]
    condition = sig.get("condition", "")
    lookback = sig.get("lookback", 50)
    fib = fibonacci_retracement(high, low, lookback=lookback)
    last_close = float(close.iloc[-1])
    level = sig.get("level", 0.618)
    fib_map = {0.236: fib.level_236, 0.382: fib.level_382, 0.5: fib.level_500, 0.618: fib.level_618, 0.786: fib.level_786}
    target = fib_map.get(level, fib.level_618)
    tolerance = abs(target - fib.swing_low) * 0.02
    if condition == "near_level":
        passed = abs(last_close - target) <= tolerance
        return passed, 0.7 if passed else 0.0, f"close={last_close:.2f} near fib {level}={target:.2f}"
    passed = last_close <= target
    return passed, 0.5 if passed else 0.0, f"close={last_close:.2f} vs fib {level}={target:.2f}"


@register_signal("sr_zone")
def _eval_sr_zone(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    from src.indicators.support_resistance import sr_zones_series
    close = market_data["close"]
    high = market_data["high"]
    low = market_data["low"]
    condition = sig.get("condition", "")
    sr = sr_zones_series(close, high, low)
    last_sr = float(sr.iloc[-1])
    threshold_val = sig.get("threshold", 0.02)
    if condition == "near_support":
        passed = last_sr < 0 and abs(last_sr) < threshold_val
        return passed, 0.6 if passed else 0.0, f"sr distance={last_sr:.4f}"
    elif condition == "near_resistance":
        passed = last_sr > 0 and abs(last_sr) < threshold_val
        return passed, 0.6 if passed else 0.0, f"sr distance={last_sr:.4f}"
    passed = abs(last_sr) < threshold_val
    return passed, 0.5 if passed else 0.0, f"sr distance={last_sr:.4f}"


@register_signal("macd_histogram")
def _eval_macd_histogram(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    from src.indicators.trend import macd as macd_fn
    result = macd_fn(market_data["close"]).histogram.dropna()
    if result.empty:
        return False, 0.0, "macd_histogram: insufficient history"
    last = float(result.iloc[-1])
    condition = sig.get("condition", "")
    threshold = float(sig.get("threshold") or 0)
    if condition == "above":
        return last > threshold, 0.5 if last > threshold else 0.0, f"macd_hist={last:.4f} > {threshold}"
    if condition == "below":
        return last < threshold, 0.5 if last < threshold else 0.0, f"macd_hist={last:.4f} < {threshold}"
    if condition == "positive":
        return last > 0, 0.5 if last > 0 else 0.0, f"macd_hist={last:.4f}"
    return False, 0.0, f"unknown macd_histogram condition: {condition}"


@register_signal("donchian")
def _eval_donchian(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    from src.indicators.trend import donchian_channel
    period = int(sig.get("period", 20))
    if len(market_data) < period + 1:
        return False, 0.0, f"donchian: need {period + 1} bars"
    dc = donchian_channel(market_data["high"], market_data["low"], period=period)
    last_close = float(market_data["close"].iloc[-1])
    prev_high = float(dc.upper.iloc[-2]) if len(dc.upper) >= 2 else float("nan")
    prev_low = float(dc.lower.iloc[-2]) if len(dc.lower) >= 2 else float("nan")
    condition = sig.get("condition", "")
    if condition == "breakout_high":
        passed = last_close > prev_high
        return passed, 0.7 if passed else 0.0, f"close={last_close:.2f} {'>' if passed else '<='} donchian_high={prev_high:.2f}"
    if condition == "breakout_low":
        passed = last_close < prev_low
        return passed, 0.7 if passed else 0.0, f"close={last_close:.2f} {'<' if passed else '>='} donchian_low={prev_low:.2f}"
    return False, 0.0, f"unknown donchian condition: {condition}"


@register_signal("gap")
def _eval_gap(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    if len(market_data) < 2:
        return False, 0.0, "gap: need 2 bars"
    prev_close = float(market_data["close"].iloc[-2])
    today_open = float(market_data["open"].iloc[-1])
    if prev_close <= 0:
        return False, 0.0, "gap: zero prev close"
    gap_pct = (today_open - prev_close) / prev_close
    condition = sig.get("condition", "")
    thr_raw = sig.get("threshold_pct")
    if thr_raw is None:
        thr_raw = sig.get("threshold")
    thr = float(thr_raw) if thr_raw is not None else 0.02
    if condition == "magnitude_above":
        passed = abs(gap_pct) > thr
        return passed, min(1.0, abs(gap_pct) / thr) if passed else 0.0, f"gap={gap_pct * 100:.2f}% {'>' if passed else '<='} {thr * 100:.2f}%"
    if condition == "up":
        passed = gap_pct > thr
        return passed, min(1.0, gap_pct / thr) if passed else 0.0, f"gap_up={gap_pct * 100:.2f}%"
    if condition == "down":
        passed = gap_pct < -thr
        return passed, min(1.0, abs(gap_pct) / thr) if passed else 0.0, f"gap_down={gap_pct * 100:.2f}%"
    return False, 0.0, f"unknown gap condition: {condition}"


@register_signal("zscore_return")
def _eval_zscore_return(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    period = int(sig.get("period", 30))
    if len(market_data) < period + 2:
        return False, 0.0, f"zscore_return: need {period + 2} bars"
    returns = market_data["close"].pct_change().dropna()
    window = returns.iloc[-period:]
    mean = float(window.mean())
    std = float(window.std())
    if std <= 0:
        return False, 0.0, "zscore_return: zero std"
    last_return = float(returns.iloc[-1])
    z = (last_return - mean) / std
    condition = sig.get("condition", "")
    thr = float(sig.get("threshold", 0))
    if condition == "below":
        passed = z < thr
        return passed, min(1.0, abs(z / thr)) if (passed and thr != 0) else (0.6 if passed else 0.0), f"zscore={z:.2f} {'<' if passed else '>='} {thr}"
    if condition == "above":
        passed = z > thr
        return passed, min(1.0, abs(z / thr)) if (passed and thr != 0) else (0.6 if passed else 0.0), f"zscore={z:.2f} {'>' if passed else '<='} {thr}"
    return False, 0.0, f"unknown zscore_return condition: {condition}"


@register_signal("fib_retrace")
def _eval_fib_retrace(sig: dict[str, Any], market_data: pd.DataFrame) -> tuple[bool, float, str]:
    lookback = int(sig.get("lookback", 30))
    if len(market_data) < lookback + 1:
        return False, 0.0, f"fib_retrace: need {lookback + 1} bars"
    window = market_data["close"].iloc[-lookback:]
    swing_hi = float(window.max())
    swing_lo = float(window.min())
    rng = swing_hi - swing_lo
    if rng <= 0:
        return False, 0.0, "fib_retrace: flat range"
    last = float(market_data["close"].iloc[-1])
    pct_from_low = (last - swing_lo) / rng
    fib_low = float(sig.get("fib_low", 0.382))
    fib_high = float(sig.get("fib_high", 0.500))
    # / pullback measured top-down
    retrace_pct = 1.0 - pct_from_low
    condition = sig.get("condition", "in_zone")
    if condition == "in_zone":
        passed = fib_low <= retrace_pct <= fib_high
        return passed, 0.6 if passed else 0.0, f"fib_retrace={retrace_pct:.3f} in [{fib_low}, {fib_high}]? {passed}"
    return False, 0.0, f"unknown fib_retrace condition: {condition}"

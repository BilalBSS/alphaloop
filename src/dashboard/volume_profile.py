from __future__ import annotations

import time
from typing import Any

import asyncpg
import structlog

from ._serialize import num as _num

logger = structlog.get_logger(__name__)

_BINS_MIN = 4
_BINS_MAX = 100
_BINS_DEFAULT = 24

_DAYS_MIN = 1
_DAYS_MAX = 365
_DAYS_DEFAULT = 30

_VALUE_AREA_PCT = 0.70

_VP_CACHE: dict[tuple, tuple[float, object]] = {}
_VP_CACHE_MAX = 64
_VP_CACHE_TTL = 30.0


def _cache_key(symbol: str, bins: int, days: int, timeframe: str) -> tuple:
    return (symbol, bins, days, timeframe)


def _cache_get(key: tuple) -> object | None:
    entry = _VP_CACHE.get(key)
    if entry is None:
        return None
    expires_at, payload = entry
    if time.monotonic() >= expires_at:
        _VP_CACHE.pop(key, None)
        return None
    return payload


def _cache_put(key: tuple, payload: object) -> None:
    if len(_VP_CACHE) >= _VP_CACHE_MAX:
        oldest_key = min(_VP_CACHE, key=lambda k: _VP_CACHE[k][0])
        _VP_CACHE.pop(oldest_key, None)
    _VP_CACHE[key] = (time.monotonic() + _VP_CACHE_TTL, payload)


def _cache_clear() -> None:
    # / test helper
    _VP_CACHE.clear()


def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n < lo:
        return lo
    if n > hi:
        return hi
    return n


def _empty_payload(symbol: str, bins: int, days: int, timeframe: str) -> dict:
    return {
        "symbol": symbol,
        "bins": [],
        "poc": None,
        "vah": None,
        "val": None,
        "total_volume": 0.0,
        "bin_count": bins,
        "days": days,
        "timeframe": timeframe,
    }


def _build_profile(
    rows: list[dict], symbol: str, bins: int, days: int, timeframe: str
) -> dict:
    closes: list[float] = []
    vols: list[float] = []
    for r in rows:
        c = _num(r.get("close"))
        v = _num(r.get("volume")) or 0.0
        if c is None:
            continue
        closes.append(c)
        vols.append(v)
    if not closes:
        return _empty_payload(symbol, bins, days, timeframe)

    price_min = min(closes)
    price_max = max(closes)
    if price_max <= price_min:
        total = sum(vols)
        bin_entry = {
            "price_low": price_min,
            "price_high": price_max,
            "volume": total,
            "pct": 100.0 if total > 0 else 0.0,
        }
        return {
            "symbol": symbol,
            "bins": [bin_entry],
            "poc": bin_entry,
            "vah": price_max,
            "val": price_min,
            "total_volume": total,
            "bin_count": 1,
            "days": days,
            "timeframe": timeframe,
        }

    step = (price_max - price_min) / bins
    volume_buckets = [0.0] * bins
    for c, v in zip(closes, vols, strict=False):
        idx = int((c - price_min) / step)
        if idx >= bins:
            idx = bins - 1
        if idx < 0:
            idx = 0
        volume_buckets[idx] += v

    total_volume = sum(volume_buckets)
    bins_out: list[dict] = []
    for i in range(bins):
        lo = price_min + i * step
        hi = price_min + (i + 1) * step
        vol = volume_buckets[i]
        pct = (vol / total_volume * 100.0) if total_volume > 0 else 0.0
        bins_out.append({
            "price_low": lo,
            "price_high": hi,
            "volume": vol,
            "pct": pct,
        })

    poc_idx = max(range(bins), key=lambda i: volume_buckets[i]) if total_volume > 0 else 0
    poc = bins_out[poc_idx]

    vah_price = poc["price_high"]
    val_price = poc["price_low"]
    if total_volume > 0:
        target = total_volume * _VALUE_AREA_PCT
        captured = volume_buckets[poc_idx]
        lo_i = poc_idx
        hi_i = poc_idx
        while captured < target and (lo_i > 0 or hi_i < bins - 1):
            up_vol = volume_buckets[hi_i + 1] if hi_i + 1 < bins else -1.0
            dn_vol = volume_buckets[lo_i - 1] if lo_i - 1 >= 0 else -1.0
            if up_vol < 0 and dn_vol < 0:
                break
            if up_vol >= dn_vol:
                hi_i += 1
                captured += up_vol
            else:
                lo_i -= 1
                captured += dn_vol
        vah_price = bins_out[hi_i]["price_high"]
        val_price = bins_out[lo_i]["price_low"]

    return {
        "symbol": symbol,
        "bins": bins_out,
        "poc": poc,
        "vah": vah_price,
        "val": val_price,
        "total_volume": total_volume,
        "bin_count": bins,
        "days": days,
        "timeframe": timeframe,
    }


async def fetch_volume_profile(
    pool: asyncpg.Pool,
    symbol: str,
    bins: int = _BINS_DEFAULT,
    days: int = _DAYS_DEFAULT,
    timeframe: str = "1Hour",
) -> dict:
    bins_clamped = _clamp(bins, _BINS_MIN, _BINS_MAX, _BINS_DEFAULT)
    days_clamped = _clamp(days, _DAYS_MIN, _DAYS_MAX, _DAYS_DEFAULT)
    if pool is None:
        return _empty_payload(symbol, bins_clamped, days_clamped, timeframe)

    key = _cache_key(symbol, bins_clamped, days_clamped, timeframe)
    cached = _cache_get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT close, volume FROM market_data_intraday
                WHERE symbol = $1 AND timeframe = $2
                    AND timestamp > NOW() - ($3 || ' days')::INTERVAL""",
                symbol,
                timeframe,
                str(days_clamped),
            )
    except asyncpg.PostgresError as exc:
        logger.debug("volume_profile_query_failed", symbol=symbol, error=str(exc))
        return _empty_payload(symbol, bins_clamped, days_clamped, timeframe)

    payload = _build_profile([dict(r) for r in rows], symbol, bins_clamped, days_clamped, timeframe)
    _cache_put(key, payload)
    return payload

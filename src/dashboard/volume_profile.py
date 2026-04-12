# / horizontal volume-at-price histogram — bins closes into price buckets and sums volume
# / adds poc (point of control), vah/val (70% value area) for quick visual anchors
# / pure observation: uses existing market_data_intraday rows, zero new tables
from __future__ import annotations

import time
from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

# / clamps matching the backend style — keep payload small + response predictable
_BINS_MIN = 4
_BINS_MAX = 100
_BINS_DEFAULT = 24

_DAYS_MIN = 1
_DAYS_MAX = 365
_DAYS_DEFAULT = 30

# / value area covers this much of total traded volume (tradingview convention)
_VALUE_AREA_PCT = 0.70

# / ttl cache mirroring /api/intraday — 30s ttl keeps data fresh without hammering the db
# / key = (symbol, bins, days, timeframe) -> (expires_at_monotonic, payload)
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


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    # / turn raw (close, volume) rows into a bins-by-price histogram + value area stats
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
        # / degenerate case: single price level — collapse to one bin
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
    for c, v in zip(closes, vols):
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

    # / poc = bin with max volume
    poc_idx = max(range(bins), key=lambda i: volume_buckets[i]) if total_volume > 0 else 0
    poc = bins_out[poc_idx]

    # / value area: expand outward from poc until 70% of total volume captured
    vah_price = poc["price_high"]
    val_price = poc["price_low"]
    if total_volume > 0:
        target = total_volume * _VALUE_AREA_PCT
        captured = volume_buckets[poc_idx]
        lo_i = poc_idx
        hi_i = poc_idx
        while captured < target and (lo_i > 0 or hi_i < bins - 1):
            # / walk outward toward the higher-volume neighbor so the area stays tight
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

# / observation-mode replay — read-only snapshot of bars + markers + signals + trades
# / within the window [min_t, cutoff]. zero re-simulation. zero side effects on agents.
# / full re-sim is deferred; see phase1-max-scope.md strategist decision.
# /
# / this module MUST NOT import or call any agent, broker, llm client, particle filter
# / or anything that mutates live state. every query is a pure SELECT against read-only rows.
# / anyone extending this file with re-sim logic violates the phase 1 scope contract.

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

# / bounds for the days_back window — mirrors /api/markers clamp
_DAYS_BACK_MIN = 1
_DAYS_BACK_MAX = 365
_DAYS_BACK_DEFAULT = 30

# / signal strength threshold matches marker_aggregator for consistency
_SIGNAL_STRENGTH_MIN = 0.5


def _iso(value: Any) -> str | None:
    # / uniform iso timestamp rendering for jsonable output
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _num(value: Any) -> float | None:
    # / safe numeric coerce — handles Decimal/None/str without raising
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_cutoff(value: str | None) -> datetime | None:
    # / accepts iso 8601 strings (with or without trailing Z); returns None on failure
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clamp_days_back(days_back: Any) -> int:
    # / coerce + clamp — tolerant of None / strings / negatives
    try:
        n = int(days_back)
    except (TypeError, ValueError):
        return _DAYS_BACK_DEFAULT
    if n < _DAYS_BACK_MIN:
        return _DAYS_BACK_MIN
    if n > _DAYS_BACK_MAX:
        return _DAYS_BACK_MAX
    return n


def _empty_bars() -> dict[str, list]:
    return {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}


def _empty_payload(symbol: str, cutoff_dt: datetime, min_t: datetime) -> dict:
    return {
        "symbol": symbol,
        "cutoff": _iso(cutoff_dt),
        "min_t": _iso(min_t),
        "max_t": _iso(cutoff_dt),
        "bars": _empty_bars(),
        "trades": [],
        "signals": [],
        "consensus": [],
    }


async def _fetch_bars(
    pool: asyncpg.Pool, symbol: str, min_t: datetime, cutoff_dt: datetime
) -> dict[str, list]:
    # / intraday candles inside [min_t, cutoff] — ascending so chart can consume directly
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT timestamp, open, high, low, close, volume
                FROM market_data_intraday
                WHERE symbol = $1 AND timestamp >= $2 AND timestamp <= $3
                ORDER BY timestamp ASC""",
                symbol,
                min_t,
                cutoff_dt,
            )
    except asyncpg.PostgresError as exc:
        logger.debug("replay_bars_query_failed", symbol=symbol, error=str(exc))
        return _empty_bars()
    out = _empty_bars()
    for r in rows:
        ts = r.get("timestamp")
        out["t"].append(_iso(ts))
        out["o"].append(_num(r.get("open")))
        out["h"].append(_num(r.get("high")))
        out["l"].append(_num(r.get("low")))
        out["c"].append(_num(r.get("close")))
        out["v"].append(_num(r.get("volume")) or 0.0)
    return out


async def _fetch_trades(
    pool: asyncpg.Pool, symbol: str, min_t: datetime, cutoff_dt: datetime
) -> list[dict]:
    # / closed + open trades visible at time t
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT created_at, side, price, strategy_id, pnl
                FROM trade_log
                WHERE symbol = $1 AND created_at >= $2 AND created_at <= $3
                ORDER BY created_at ASC""",
                symbol,
                min_t,
                cutoff_dt,
            )
    except asyncpg.PostgresError as exc:
        logger.debug("replay_trades_query_failed", symbol=symbol, error=str(exc))
        return []
    out: list[dict] = []
    for r in rows:
        side = (r.get("side") or "").lower()
        out.append({
            "time": _iso(r.get("created_at")),
            "price": _num(r.get("price")),
            "side": "buy" if side in ("buy", "long") else "sell",
            "strategy_id": r.get("strategy_id"),
            "pnl": _num(r.get("pnl")),
        })
    return out


async def _fetch_signals(
    pool: asyncpg.Pool, symbol: str, min_t: datetime, cutoff_dt: datetime
) -> list[dict]:
    # / trade signals above the strength threshold (matches marker_aggregator)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT created_at, signal_type, strength, strategy_id
                FROM trade_signals
                WHERE symbol = $1 AND created_at >= $2 AND created_at <= $3
                    AND strength >= $4
                ORDER BY created_at ASC""",
                symbol,
                min_t,
                cutoff_dt,
                _SIGNAL_STRENGTH_MIN,
            )
    except asyncpg.PostgresError as exc:
        logger.debug("replay_signals_query_failed", symbol=symbol, error=str(exc))
        return []
    out: list[dict] = []
    for r in rows:
        strength = _num(r.get("strength"))
        if strength is None:
            continue
        sig_type = (r.get("signal_type") or "").lower()
        action = "buy" if sig_type == "buy" else "sell"
        out.append({
            "time": _iso(r.get("created_at")),
            "action": action,
            "strength": strength,
            "strategy_id": r.get("strategy_id"),
        })
    return out


async def _fetch_consensus(
    pool: asyncpg.Pool, symbol: str, min_t: datetime, cutoff_dt: datetime
) -> list[dict]:
    # / latest dual-llm consensus per day inside the window, extracted from analysis_scores.details
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT DISTINCT ON (date) date, details->>'ai_consensus' as consensus
                FROM analysis_scores
                WHERE symbol = $1 AND date >= $2::DATE AND date <= $3::DATE
                ORDER BY date ASC, created_at DESC""",
                symbol,
                min_t,
                cutoff_dt,
            )
    except asyncpg.PostgresError as exc:
        logger.debug("replay_consensus_query_failed", symbol=symbol, error=str(exc))
        return []
    out: list[dict] = []
    for r in rows:
        consensus = (r.get("consensus") or "").lower() or None
        if consensus not in ("bullish", "bearish", "neutral", "disagree"):
            continue
        out.append({
            "time": _iso(r.get("date")),
            "consensus": consensus,
        })
    return out


async def fetch_replay_snapshot(
    pool: asyncpg.Pool,
    symbol: str,
    cutoff_iso: str,
    days_back: int = _DAYS_BACK_DEFAULT,
) -> dict:
    # / observation-only snapshot of everything knowable at time t
    # / invalid cutoff_iso -> defaults to NOW (utc) so the chart falls back to live view
    # / days_back is clamped into [1, 365]
    days = _clamp_days_back(days_back)
    cutoff_dt = _parse_cutoff(cutoff_iso)
    if cutoff_dt is None:
        cutoff_dt = datetime.now(timezone.utc)
    # / normalize naive datetimes to utc so asyncpg comparisons against TIMESTAMPTZ succeed
    if cutoff_dt.tzinfo is None:
        cutoff_dt = cutoff_dt.replace(tzinfo=timezone.utc)
    min_t = cutoff_dt - timedelta(days=days)

    if pool is None:
        return _empty_payload(symbol, cutoff_dt, min_t)

    # / all four queries hit independent tables — run in parallel so one replay fetch
    # / is one round-trip instead of four sequential awaits
    bars_res, trades_res, signals_res, consensus_res = await asyncio.gather(
        _fetch_bars(pool, symbol, min_t, cutoff_dt),
        _fetch_trades(pool, symbol, min_t, cutoff_dt),
        _fetch_signals(pool, symbol, min_t, cutoff_dt),
        _fetch_consensus(pool, symbol, min_t, cutoff_dt),
        return_exceptions=True,
    )
    def _unwrap(res, empty):
        if isinstance(res, Exception):
            logger.debug("replay_fetch_failed", error=str(res))
            return empty
        return res
    bars = _unwrap(bars_res, {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []})
    trades = _unwrap(trades_res, [])
    signals = _unwrap(signals_res, [])
    consensus = _unwrap(consensus_res, [])

    return {
        "symbol": symbol,
        "cutoff": _iso(cutoff_dt),
        "min_t": _iso(min_t),
        "max_t": _iso(cutoff_dt),
        "bars": bars,
        "trades": trades,
        "signals": signals,
        "consensus": consensus,
    }

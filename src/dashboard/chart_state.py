# / chart state persistence — per-symbol selected timeframe + active indicators + params
from __future__ import annotations

import json
from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)

# / whitelisted timeframes matching orchestrator intraday bars
VALID_TIMEFRAMES: set[str] = {"1Min", "5Min", "15Min", "1Hour", "1Day"}

# / size cap for indicator_params jsonb — mirrors the drawings payload cap pattern
# / prevents a malicious client from bloating the db with arbitrarily large blobs
_PARAMS_MAX_BYTES = 16 * 1024
_INDICATOR_LIST_MAX = 128


def validate_indicator_params(params: Any) -> bool:
    # / minimal sanity: dict, serializable, under the size cap
    if not isinstance(params, dict):
        return False
    try:
        encoded = json.dumps(params)
    except (TypeError, ValueError):
        return False
    return len(encoded) <= _PARAMS_MAX_BYTES


def _default_state(symbol: str) -> dict:
    # / fallback state when a symbol has no row yet
    return {
        "symbol": symbol,
        "timeframe": "1Hour",
        "active_indicators": [],
        "indicator_params": {},
    }


def _parse_jsonb(value: Any, fallback: Any) -> Any:
    # / asyncpg returns jsonb as str or native — normalize both
    if value is None:
        return fallback
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return fallback
    return fallback


def _row_to_state(row: dict) -> dict:
    return {
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe") or "1Hour",
        "active_indicators": _parse_jsonb(row.get("active_indicators"), []),
        "indicator_params": _parse_jsonb(row.get("indicator_params"), {}),
    }


async def get_chart_state(pool: asyncpg.Pool, symbol: str) -> dict:
    # / returns persisted state or default if no row exists
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT symbol, timeframe, active_indicators, indicator_params
                FROM user_chart_state WHERE symbol = $1""",
                symbol,
            )
    except Exception as exc:
        logger.debug("chart_state_fetch_failed", symbol=symbol, error=str(exc))
        return _default_state(symbol)
    if row is None:
        return _default_state(symbol)
    return _row_to_state(dict(row))


async def upsert_chart_state(
    pool: asyncpg.Pool,
    symbol: str,
    timeframe: str | None = None,
    active_indicators: list[str] | None = None,
    indicator_params: dict[str, Any] | None = None,
) -> dict:
    # / upsert per-symbol row, only fields that are not none are updated (coalesce)
    # / returns the updated state dict
    # / drop invalid timeframe silently to keep api forgiving
    if timeframe is not None and timeframe not in VALID_TIMEFRAMES:
        timeframe = None
    # / defense in depth: cap the indicator list and reject oversized params payloads
    # / silently drop invalid params rather than failing the whole upsert
    if active_indicators is not None and len(active_indicators) > _INDICATOR_LIST_MAX:
        active_indicators = active_indicators[:_INDICATOR_LIST_MAX]
    if indicator_params is not None and not validate_indicator_params(indicator_params):
        indicator_params = None
    indicators_json = json.dumps(active_indicators) if active_indicators is not None else None
    params_json = json.dumps(indicator_params) if indicator_params is not None else None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO user_chart_state (symbol, timeframe, active_indicators, indicator_params, updated_at)
                VALUES ($1, COALESCE($2, '1Hour'), COALESCE($3::jsonb, '[]'::jsonb), COALESCE($4::jsonb, '{}'::jsonb), NOW())
                ON CONFLICT (symbol) DO UPDATE SET
                    timeframe = COALESCE($2, user_chart_state.timeframe),
                    active_indicators = COALESCE($3::jsonb, user_chart_state.active_indicators),
                    indicator_params = COALESCE($4::jsonb, user_chart_state.indicator_params),
                    updated_at = NOW()
                RETURNING symbol, timeframe, active_indicators, indicator_params""",
                symbol,
                timeframe,
                indicators_json,
                params_json,
            )
    except Exception as exc:
        logger.debug("chart_state_upsert_failed", symbol=symbol, error=str(exc))
        return _default_state(symbol)
    if row is None:
        return _default_state(symbol)
    return _row_to_state(dict(row))


def sanitize_indicators(ids: list[str]) -> list[str]:
    # / filter to known-valid ids from the registry, preserve order, dedupe
    from src.dashboard import indicator_registry
    valid = set(indicator_registry.available_indicators())
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if isinstance(i, str) and i in valid and i not in seen:
            out.append(i)
            seen.add(i)
    return out

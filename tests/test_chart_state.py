# / tests for dashboard chart_state persistence

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.dashboard.chart_state import (
    VALID_TIMEFRAMES,
    _default_state,
    _parse_jsonb,
    _row_to_state,
    get_chart_state,
    sanitize_indicators,
    upsert_chart_state,
    validate_indicator_params,
)


def _mock_pool():
    # / standard asyncpg pool mock, mirrors tests/test_loop_registry.py
    pool = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


def test_valid_timeframes_whitelist_matches_orchestrator_bars():
    # / whitelist must match orchestrator intraday bars
    assert {"1Min", "5Min", "15Min", "1Hour", "1Day"} == VALID_TIMEFRAMES


def test_default_state_shape():
    # / fallback state carries symbol and sensible defaults
    state = _default_state("AAPL")
    assert state == {
        "symbol": "AAPL",
        "timeframe": "1Hour",
        "active_indicators": [],
        "indicator_params": {},
    }


def test_validate_indicator_params_accepts_small_dict():
    assert validate_indicator_params({"sma_20": {"period": 20}}) is True


def test_validate_indicator_params_rejects_non_dict():
    assert validate_indicator_params([1, 2, 3]) is False
    assert validate_indicator_params("string") is False
    assert validate_indicator_params(None) is False


def test_validate_indicator_params_rejects_non_serializable():
    class Unserializable:
        pass
    assert validate_indicator_params({"x": Unserializable()}) is False


def test_validate_indicator_params_rejects_oversized_payload():
    # / boundary: exactly over 16kb cap should fail
    huge = {"k": "x" * (16 * 1024 + 10)}
    assert validate_indicator_params(huge) is False


def test_validate_indicator_params_accepts_at_boundary():
    # / keep under 16kb; small json overhead preserved
    value = "x" * (16 * 1024 - 100)
    assert validate_indicator_params({"k": value}) is True


def test_parse_jsonb_native_dict_passthrough():
    assert _parse_jsonb({"a": 1}, fallback={}) == {"a": 1}


def test_parse_jsonb_native_list_passthrough():
    assert _parse_jsonb([1, 2, 3], fallback=[]) == [1, 2, 3]


def test_parse_jsonb_none_returns_fallback():
    assert _parse_jsonb(None, fallback={"default": True}) == {"default": True}


def test_parse_jsonb_valid_json_string():
    assert _parse_jsonb('{"a": 1}', fallback={}) == {"a": 1}


def test_parse_jsonb_invalid_json_returns_fallback():
    assert _parse_jsonb("not json", fallback=[]) == []


def test_row_to_state_normalizes_timeframe():
    row = {
        "symbol": "MSFT",
        "timeframe": None,
        "active_indicators": None,
        "indicator_params": None,
    }
    out = _row_to_state(row)
    # / missing timeframe collapses to default '1Hour'
    assert out["timeframe"] == "1Hour"
    assert out["active_indicators"] == []
    assert out["indicator_params"] == {}


@pytest.mark.asyncio
async def test_get_chart_state_returns_default_when_row_missing():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(return_value=None)
    out = await get_chart_state(pool, "AAPL")
    assert out == _default_state("AAPL")


@pytest.mark.asyncio
async def test_get_chart_state_returns_default_on_db_error():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(side_effect=Exception("conn dropped"))
    out = await get_chart_state(pool, "AAPL")
    assert out == _default_state("AAPL")


@pytest.mark.asyncio
async def test_get_chart_state_returns_parsed_row():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(return_value={
        "symbol": "AAPL",
        "timeframe": "5Min",
        "active_indicators": '["sma_20","rsi_14"]',
        "indicator_params": '{"sma_20":{"period":20}}',
    })
    out = await get_chart_state(pool, "AAPL")
    assert out["symbol"] == "AAPL"
    assert out["timeframe"] == "5Min"
    assert out["active_indicators"] == ["sma_20", "rsi_14"]
    assert out["indicator_params"] == {"sma_20": {"period": 20}}


@pytest.mark.asyncio
async def test_upsert_drops_invalid_timeframe_silently():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(return_value={
        "symbol": "AAPL",
        "timeframe": "1Hour",
        "active_indicators": [],
        "indicator_params": {},
    })
    await upsert_chart_state(pool, "AAPL", timeframe="99Min")
    # / fetchrow signature: (sql, symbol, timeframe, indicators_json, params_json)
    args = conn.fetchrow.call_args.args
    assert args[2] is None  # / invalid tf coerced to None so coalesce keeps existing


@pytest.mark.asyncio
async def test_upsert_caps_indicator_list_at_128():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(return_value={
        "symbol": "AAPL", "timeframe": "1Hour",
        "active_indicators": [], "indicator_params": {},
    })
    oversized = [f"ind_{i}" for i in range(200)]
    await upsert_chart_state(pool, "AAPL", active_indicators=oversized)
    args = conn.fetchrow.call_args.args
    # / fetchrow signature: (sql, symbol, timeframe, indicators_json, params_json)
    indicators = json.loads(args[3])
    assert len(indicators) == 128


@pytest.mark.asyncio
async def test_upsert_drops_oversized_params():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(return_value={
        "symbol": "AAPL", "timeframe": "1Hour",
        "active_indicators": [], "indicator_params": {},
    })
    oversized_params = {"k": "x" * (16 * 1024 + 10)}
    await upsert_chart_state(pool, "AAPL", indicator_params=oversized_params)
    args = conn.fetchrow.call_args.args
    # / oversized -> None so coalesce keeps existing
    assert args[3] is None


@pytest.mark.asyncio
async def test_upsert_returns_default_on_db_error():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(side_effect=Exception("upsert failed"))
    out = await upsert_chart_state(pool, "AAPL", timeframe="1Day")
    assert out == _default_state("AAPL")


def test_sanitize_indicators_filters_unknown_and_dedupes(monkeypatch):
    # / monkeypatch the registry call to control the valid set
    from src.dashboard import indicator_registry

    def fake_available():
        return ["sma_20", "rsi_14"]

    monkeypatch.setattr(indicator_registry, "available_indicators", fake_available)
    result = sanitize_indicators(["sma_20", "rsi_14", "sma_20", "unknown", 42, "rsi_14"])
    # / dedupe preserves first occurrence order, drops unknown + non-strings
    assert result == ["sma_20", "rsi_14"]


def test_sanitize_indicators_empty_input():
    result = sanitize_indicators([])
    assert result == []

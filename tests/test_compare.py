# / tests for dashboard compare (pair normalization overlay)

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from src.dashboard.compare import _clamp_days, _iso, _num, fetch_compare


def _mock_pool():
    pool = MagicMock()
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


def test_clamp_days_default_when_invalid():
    assert _clamp_days(None) == 90
    assert _clamp_days("abc") == 90


def test_clamp_days_lower_boundary():
    # / 0 clamps to 1
    assert _clamp_days(0) == 1
    assert _clamp_days(-50) == 1


def test_clamp_days_upper_boundary():
    assert _clamp_days(400) == 365
    assert _clamp_days(365) == 365


def test_clamp_days_within_range_unchanged():
    assert _clamp_days(90) == 90
    assert _clamp_days(30) == 30


def test_iso_none():
    assert _iso(None) is None


def test_iso_datetime():
    dt = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    assert _iso(dt) == dt.isoformat()


def test_iso_string_passthrough():
    assert _iso("2026-04-19") == "2026-04-19"


def test_num_coerces_int_and_float():
    assert _num(5) == 5.0
    assert _num("1.5") == 1.5
    assert _num(None) is None
    assert _num("nope") is None


@pytest.mark.asyncio
async def test_fetch_compare_null_pool_returns_empty():
    out = await fetch_compare(None, "AAPL", "MSFT")
    assert out["base"] == "AAPL"
    assert out["against"] == "MSFT"
    assert out["common_count"] == 0
    assert out["base_series"] == []


@pytest.mark.asyncio
async def test_fetch_compare_no_overlap_returns_empty():
    # / base has dates A,B,C and against has D,E — no intersection
    pool, conn = _mock_pool()
    base_rows = [
        {"date": date(2026, 4, 1), "close": 100.0},
        {"date": date(2026, 4, 2), "close": 101.0},
    ]
    against_rows = [
        {"date": date(2026, 5, 1), "close": 50.0},
        {"date": date(2026, 5, 2), "close": 51.0},
    ]
    conn.fetch = AsyncMock(side_effect=[base_rows, against_rows])
    out = await fetch_compare(pool, "AAPL", "MSFT", timeframe="1Day")
    assert out["common_count"] == 0
    assert out["base_series"] == []


@pytest.mark.asyncio
async def test_fetch_compare_happy_path_normalization():
    # / hand-computed: base 100 -> 110 -> 105 = 0%, +10%, +5%
    # / against 50 -> 52.5 -> 51 = 0%, +5%, +2%
    pool, conn = _mock_pool()
    d1, d2, d3 = date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3)
    base_rows = [
        {"date": d1, "close": 100.0},
        {"date": d2, "close": 110.0},
        {"date": d3, "close": 105.0},
    ]
    against_rows = [
        {"date": d1, "close": 50.0},
        {"date": d2, "close": 52.5},
        {"date": d3, "close": 51.0},
    ]
    conn.fetch = AsyncMock(side_effect=[base_rows, against_rows])
    out = await fetch_compare(pool, "AAPL", "MSFT", timeframe="1Day")
    assert out["common_count"] == 3
    assert out["base_series"][0]["value"] == pytest.approx(0.0)
    assert out["base_series"][1]["value"] == pytest.approx(10.0)
    assert out["base_series"][2]["value"] == pytest.approx(5.0)
    assert out["against_series"][0]["value"] == pytest.approx(0.0)
    assert out["against_series"][1]["value"] == pytest.approx(5.0)
    assert out["against_series"][2]["value"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_fetch_compare_zero_first_close_returns_empty():
    # / division by zero guard: first close of 0 should trip the empty return
    pool, conn = _mock_pool()
    d1 = date(2026, 4, 1)
    base_rows = [{"date": d1, "close": 0.0}]
    against_rows = [{"date": d1, "close": 50.0}]
    conn.fetch = AsyncMock(side_effect=[base_rows, against_rows])
    out = await fetch_compare(pool, "AAPL", "MSFT", timeframe="1Day")
    assert out["common_count"] == 0
    assert out["base_series"] == []


@pytest.mark.asyncio
async def test_fetch_compare_partial_overlap_intersection():
    # / base has A,B,C, against has B,C,D — intersection is B,C only
    pool, conn = _mock_pool()
    dA, dB, dC, dD = (date(2026, 4, i) for i in (1, 2, 3, 4))
    base_rows = [
        {"date": dA, "close": 100.0},
        {"date": dB, "close": 110.0},
        {"date": dC, "close": 120.0},
    ]
    against_rows = [
        {"date": dB, "close": 50.0},
        {"date": dC, "close": 55.0},
        {"date": dD, "close": 60.0},
    ]
    conn.fetch = AsyncMock(side_effect=[base_rows, against_rows])
    out = await fetch_compare(pool, "AAPL", "MSFT", timeframe="1Day")
    assert out["common_count"] == 2
    # / B is first in both -> 0%, C -> +9.09% vs B for base, +10% vs B for against
    assert out["base_series"][0]["value"] == pytest.approx(0.0)
    assert out["base_series"][1]["value"] == pytest.approx((120.0 / 110.0 - 1.0) * 100.0)
    assert out["against_series"][1]["value"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_fetch_compare_empty_data_returns_empty():
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[])
    out = await fetch_compare(pool, "AAPL", "MSFT", timeframe="1Day")
    assert out["common_count"] == 0
    assert out["base_series"] == []


@pytest.mark.asyncio
async def test_fetch_compare_intraday_routes_to_intraday_table():
    # / non-daily timeframe must hit market_data_intraday with timeframe arg
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[])
    await fetch_compare(pool, "AAPL", "MSFT", timeframe="1Hour", days=5)
    # / two calls to fetch — verify both passed the intraday sql
    for call in conn.fetch.call_args_list:
        sql = call.args[0]
        assert "market_data_intraday" in sql


@pytest.mark.asyncio
async def test_fetch_compare_db_error_returns_empty():
    # / asyncpg.PostgresError should not crash the function
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(side_effect=asyncpg.PostgresError("nope"))
    out = await fetch_compare(pool, "AAPL", "MSFT", timeframe="1Day")
    assert out["common_count"] == 0


@pytest.mark.asyncio
async def test_fetch_compare_clamps_days_in_response():
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[])
    out = await fetch_compare(pool, "AAPL", "MSFT", days=999)
    assert out["days"] == 365

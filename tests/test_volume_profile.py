# / tests for dashboard volume_profile (price-volume histogram + value area)

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from src.dashboard import volume_profile
from src.dashboard.volume_profile import (
    _build_profile,
    _cache_clear,
    _cache_get,
    _cache_key,
    _cache_put,
    _clamp,
    _empty_payload,
    fetch_volume_profile,
)


def _mock_pool():
    pool = MagicMock()
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


@pytest.fixture(autouse=True)
def _clear_cache():
    # / prevent cache bleed across tests
    _cache_clear()
    yield
    _cache_clear()


def test_clamp_defaults_when_invalid():
    assert _clamp(None, 4, 100, 24) == 24
    assert _clamp("bad", 4, 100, 24) == 24


def test_clamp_lower_boundary():
    assert _clamp(1, 4, 100, 24) == 4


def test_clamp_upper_boundary():
    assert _clamp(500, 4, 100, 24) == 100


def test_clamp_in_range():
    assert _clamp(50, 4, 100, 24) == 50


def test_empty_payload_shape():
    out = _empty_payload("AAPL", 24, 30, "1Hour")
    assert out["symbol"] == "AAPL"
    assert out["bins"] == []
    assert out["poc"] is None
    assert out["total_volume"] == 0.0
    assert out["bin_count"] == 24


def test_build_profile_empty_rows_returns_empty_payload():
    out = _build_profile([], "AAPL", 24, 30, "1Hour")
    assert out["bins"] == []
    assert out["poc"] is None


def test_build_profile_single_price_collapses_to_one_bin():
    # / degenerate case: every close at 100 -> one bin, entire volume
    rows = [{"close": 100.0, "volume": 500.0} for _ in range(5)]
    out = _build_profile(rows, "AAPL", 10, 30, "1Hour")
    assert len(out["bins"]) == 1
    assert out["bins"][0]["volume"] == 2500.0
    assert out["bins"][0]["pct"] == 100.0
    assert out["poc"]["volume"] == 2500.0


def test_build_profile_binning_hand_computed():
    # / 4 bins over 100..108 -> step=2; closes/volumes:
    # /   100 vol=10 -> bin 0 [100,102)
    # /   103 vol=20 -> bin 1 [102,104)
    # /   105 vol=50 -> bin 2 [104,106)  <- poc
    # /   107 vol=30 -> bin 3 [106,108]
    rows = [
        {"close": 100.0, "volume": 10.0},
        {"close": 103.0, "volume": 20.0},
        {"close": 105.0, "volume": 50.0},
        {"close": 107.0, "volume": 30.0},
    ]
    out = _build_profile(rows, "AAPL", 4, 30, "1Hour")
    assert out["total_volume"] == 110.0
    assert len(out["bins"]) == 4
    # / bin 2 should be poc with volume 50
    assert out["poc"]["volume"] == 50.0
    # / pct check: 50/110 * 100 = 45.454...
    assert out["bins"][2]["pct"] == pytest.approx(50.0 / 110.0 * 100.0)
    # / total pct sums to 100
    assert sum(b["pct"] for b in out["bins"]) == pytest.approx(100.0)


def test_build_profile_max_price_lands_in_last_bin():
    # / value at exact price_max (idx would be bins out of range) is clamped to last bin
    rows = [
        {"close": 100.0, "volume": 10.0},
        {"close": 110.0, "volume": 5.0},   # / price_max -> last bin
    ]
    out = _build_profile(rows, "AAPL", 5, 30, "1Hour")
    assert out["bins"][-1]["volume"] == 5.0


def test_build_profile_value_area_captures_70_percent():
    # / 10 bins, synthesized peaks so va walks outward from poc
    # / concentration: bin 4 has 70% of volume -> va should be just that bin
    rows: list[dict] = []
    # / 10 bars at 100..109 with tiny volume
    for i in range(10):
        rows.append({"close": 100.0 + i, "volume": 1.0})
    # / stuff bin containing 104 with huge volume
    for _ in range(30):
        rows.append({"close": 104.0, "volume": 10.0})
    out = _build_profile(rows, "AAPL", 10, 30, "1Hour")
    # / total vol = 10 + 300 = 310, 70% = 217
    assert out["total_volume"] == pytest.approx(310.0)
    # / poc bin alone holds 300 (> 217) so va is one bin wide
    poc = out["poc"]
    assert poc["volume"] >= 217.0
    assert out["vah"] == poc["price_high"]
    assert out["val"] == poc["price_low"]


def test_build_profile_bin_count_matches_requested():
    rows = [
        {"close": 100.0 + i * 0.5, "volume": 10.0} for i in range(20)
    ]
    out = _build_profile(rows, "AAPL", 8, 30, "1Hour")
    assert len(out["bins"]) == 8


def test_cache_put_and_get_roundtrip():
    key = _cache_key("AAPL", 24, 30, "1Hour")
    payload = {"dummy": True}
    _cache_put(key, payload)
    assert _cache_get(key) == payload


def test_cache_eviction_at_capacity():
    # / fill past _VP_CACHE_MAX=64 -> oldest kicked out
    for i in range(70):
        _cache_put(_cache_key(f"S{i}", 24, 30, "1Hour"), {"n": i})
    assert len(volume_profile._VP_CACHE) <= volume_profile._VP_CACHE_MAX


@pytest.mark.asyncio
async def test_fetch_null_pool_returns_empty():
    out = await fetch_volume_profile(None, "AAPL")
    assert out["bins"] == []
    assert out["bin_count"] == 24
    assert out["days"] == 30


@pytest.mark.asyncio
async def test_fetch_happy_path():
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[
        {"close": 100.0, "volume": 10.0},
        {"close": 105.0, "volume": 20.0},
        {"close": 110.0, "volume": 30.0},
    ])
    out = await fetch_volume_profile(pool, "AAPL", bins=4, days=30, timeframe="1Hour")
    assert out["total_volume"] == 60.0
    assert out["symbol"] == "AAPL"
    # / second call with same args should hit cache (conn.fetch not called again)
    call_count_before = conn.fetch.call_count
    await fetch_volume_profile(pool, "AAPL", bins=4, days=30, timeframe="1Hour")
    assert conn.fetch.call_count == call_count_before


@pytest.mark.asyncio
async def test_fetch_db_error_returns_empty():
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(side_effect=asyncpg.PostgresError("nope"))
    out = await fetch_volume_profile(pool, "AAPL")
    assert out["bins"] == []


@pytest.mark.asyncio
async def test_fetch_clamps_bins_and_days():
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[])
    out = await fetch_volume_profile(pool, "AAPL", bins=999, days=999)
    assert out["bin_count"] == 100
    assert out["days"] == 365

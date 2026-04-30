# / tests for dashboard replay (observation-only snapshot)

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from src.dashboard.replay import (
    _clamp_days_back,
    _iso,
    _num,
    _parse_cutoff,
    fetch_replay_snapshot,
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


def test_clamp_days_back_default_on_invalid():
    assert _clamp_days_back(None) == 30
    assert _clamp_days_back("abc") == 30


def test_clamp_days_back_lower_boundary():
    assert _clamp_days_back(0) == 1
    assert _clamp_days_back(-10) == 1


def test_clamp_days_back_upper_boundary():
    assert _clamp_days_back(1000) == 365
    assert _clamp_days_back(365) == 365


def test_clamp_days_back_in_range_unchanged():
    assert _clamp_days_back(45) == 45


def test_parse_cutoff_none():
    assert _parse_cutoff(None) is None
    assert _parse_cutoff("") is None
    assert _parse_cutoff("   ") is None


def test_parse_cutoff_not_string():
    assert _parse_cutoff(123) is None  # type: ignore[arg-type]


def test_parse_cutoff_valid_iso():
    dt = _parse_cutoff("2026-04-19T12:00:00+00:00")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 4 and dt.day == 19


def test_parse_cutoff_trailing_z():
    # / trailing Z should be normalized to +00:00
    dt = _parse_cutoff("2026-04-19T12:00:00Z")
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_cutoff_bad_iso():
    assert _parse_cutoff("nope") is None


def test_iso_none():
    assert _iso(None) is None


def test_iso_datetime():
    dt = datetime(2026, 4, 19, tzinfo=timezone.utc)
    assert _iso(dt) == dt.isoformat()


def test_num_safe_coerce():
    assert _num(5) == 5.0
    assert _num("1.5") == 1.5
    assert _num(None) is None
    assert _num("nope") is None


@pytest.mark.asyncio
async def test_replay_null_pool_returns_empty_payload():
    out = await fetch_replay_snapshot(None, "AAPL", "2026-04-19T12:00:00Z", days_back=30)
    assert out["symbol"] == "AAPL"
    assert out["bars"] == {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    assert out["trades"] == []
    assert out["signals"] == []
    assert out["consensus"] == []


@pytest.mark.asyncio
async def test_replay_invalid_cutoff_defaults_to_now():
    # / invalid cutoff -> now(utc), snapshot still returns successfully
    out = await fetch_replay_snapshot(None, "AAPL", "garbage", days_back=30)
    assert out["symbol"] == "AAPL"
    # / cutoff should have been populated (not None)
    assert out["cutoff"] is not None


@pytest.mark.asyncio
async def test_replay_fetches_all_four_tables():
    # / bars, trades, signals, consensus all populated from 4 separate fetch calls
    pool, conn = _mock_pool()
    ts = datetime(2026, 4, 10, 10, 0, tzinfo=timezone.utc)
    bar_rows = [{
        "timestamp": ts, "open": 100.0, "high": 101.0,
        "low": 99.0, "close": 100.5, "volume": 10000.0,
    }]
    trade_rows = [{
        "created_at": ts, "side": "buy", "price": 100.5,
        "strategy_id": 1, "pnl": 5.0,
    }]
    signal_rows = [{
        "created_at": ts, "signal_type": "buy",
        "strength": 0.8, "strategy_id": 1,
    }]
    consensus_rows = [{"date": date(2026, 4, 10), "consensus": "bullish"}]
    conn.fetch = AsyncMock(side_effect=[bar_rows, trade_rows, signal_rows, consensus_rows])
    out = await fetch_replay_snapshot(pool, "AAPL", "2026-04-19T00:00:00Z")
    assert out["bars"]["t"] == [ts.isoformat()]
    assert out["bars"]["c"] == [100.5]
    assert out["trades"][0]["side"] == "buy"
    assert out["signals"][0]["action"] == "buy"
    assert out["consensus"][0]["consensus"] == "bullish"


@pytest.mark.asyncio
async def test_replay_trades_normalizes_long_to_buy():
    # / trade_log can store 'long' side — must map to 'buy' for chart consumption
    pool, conn = _mock_pool()
    ts = datetime(2026, 4, 10, tzinfo=timezone.utc)
    conn.fetch = AsyncMock(side_effect=[
        [],  # / bars
        [{"created_at": ts, "side": "long", "price": 50.0, "strategy_id": 1, "pnl": 0}],
        [],
        [],
    ])
    out = await fetch_replay_snapshot(pool, "AAPL", "2026-04-19T00:00:00Z")
    assert out["trades"][0]["side"] == "buy"


@pytest.mark.asyncio
async def test_replay_filters_invalid_consensus_values():
    # / anything other than bullish/bearish/neutral/disagree dropped
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(side_effect=[
        [], [], [],
        [
            {"date": date(2026, 4, 10), "consensus": "bullish"},
            {"date": date(2026, 4, 11), "consensus": "weird_value"},
            {"date": date(2026, 4, 12), "consensus": None},
        ],
    ])
    out = await fetch_replay_snapshot(pool, "AAPL", "2026-04-19T00:00:00Z")
    assert len(out["consensus"]) == 1
    assert out["consensus"][0]["consensus"] == "bullish"


@pytest.mark.asyncio
async def test_replay_db_error_per_table_still_returns_skeleton():
    # / postgres errors per table should not crash the snapshot
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(side_effect=asyncpg.PostgresError("nope"))
    out = await fetch_replay_snapshot(pool, "AAPL", "2026-04-19T00:00:00Z")
    assert out["bars"]["t"] == []
    assert out["trades"] == []
    assert out["signals"] == []
    assert out["consensus"] == []


@pytest.mark.asyncio
async def test_replay_signals_drop_none_strength():
    # / a row with missing strength should be skipped
    pool, conn = _mock_pool()
    ts = datetime(2026, 4, 10, tzinfo=timezone.utc)
    conn.fetch = AsyncMock(side_effect=[
        [],  # bars
        [],  # trades
        [
            {"created_at": ts, "signal_type": "buy", "strength": None, "strategy_id": 1},
            {"created_at": ts, "signal_type": "sell", "strength": 0.7, "strategy_id": 2},
        ],
        [],
    ])
    out = await fetch_replay_snapshot(pool, "AAPL", "2026-04-19T00:00:00Z")
    assert len(out["signals"]) == 1
    assert out["signals"][0]["action"] == "sell"


@pytest.mark.asyncio
async def test_replay_normalizes_naive_cutoff_to_utc():
    # / naive datetime cutoff should be injected with utc tzinfo to match TIMESTAMPTZ
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[])
    # / this iso string lacks tzinfo
    out = await fetch_replay_snapshot(pool, "AAPL", "2026-04-19T12:00:00")
    # / cutoff output should include a tz offset (e.g. +00:00)
    assert out["cutoff"] is not None
    assert "+00:00" in out["cutoff"]


@pytest.mark.asyncio
async def test_replay_clamps_days_back_in_window():
    # / 9999 should clamp to 365, min_t should be exactly 365 days before cutoff
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[])
    out = await fetch_replay_snapshot(pool, "AAPL", "2026-04-19T00:00:00Z", days_back=9999)
    cutoff = datetime.fromisoformat(out["cutoff"])
    min_t = datetime.fromisoformat(out["min_t"])
    delta_days = (cutoff - min_t).days
    assert delta_days == 365

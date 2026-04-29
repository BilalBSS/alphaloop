# / phase 6 step 1: loop registry — metadata, describe, trigger queue

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.data import loop_registry


def _mock_pool() -> MagicMock:
    # / asyncpg pool -> async context manager -> connection mock pattern
    pool = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=None)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


def test_loop_metadata_contains_core_services():
    # / every service the orchestrator launches must be in the metadata map
    core = {
        "analyst", "deepseek", "strategy", "evolution",
        "macro_backfill", "fundamentals_backfill", "insider_backfill",
        "regime_backfill", "daily_bar_backfill", "intraday_backfill",
        "crypto_backfill", "price_refresh", "alpaca_sync",
        "alternative_data", "strategy_metrics", "monitoring", "cost_flush",
        "alert", "reasoner", "wiki_embedding", "wiki_archive",
        "knowledge_hydration", "risk", "executor",
    }
    assert core.issubset(set(loop_registry.LOOP_METADATA.keys()))


def test_interval_metadata_has_cadence_seconds():
    for name, meta in loop_registry.LOOP_METADATA.items():
        if meta["kind"] == "interval":
            assert meta.get("cadence_seconds") is not None, f"{name} missing cadence_seconds"
            assert meta.get("cadence_seconds") > 0


def test_cron_metadata_has_cron_hour_et():
    for name, meta in loop_registry.LOOP_METADATA.items():
        if meta["kind"] == "cron":
            assert meta.get("cron_hour_et") is not None, f"{name} missing cron_hour_et"
            assert 0 <= meta.get("cron_hour_et") < 24


def test_compute_next_fire_interval():
    meta = {"kind": "interval", "cadence_seconds": 3600}
    base = datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc)
    next_ts = loop_registry._compute_next_fire(meta, base=base)
    assert next_ts is not None
    delta = (next_ts - base).total_seconds()
    assert abs(delta - 3600) < 1


def test_compute_next_fire_cron_ahead_today():
    # / 11:00 UTC -> cron @ 9am ET (13:00 UTC standard) should be later today
    meta = {"kind": "cron", "cron_hour_et": 9}
    base = datetime(2026, 4, 19, 11, 0, 0, tzinfo=timezone.utc)
    next_ts = loop_registry._compute_next_fire(meta, base=base)
    assert next_ts is not None
    assert next_ts > base


def test_compute_next_fire_cron_past_today():
    # / past 5pm ET means next fire is tomorrow 5pm ET
    meta = {"kind": "cron", "cron_hour_et": 17}
    base = datetime(2026, 4, 19, 23, 0, 0, tzinfo=timezone.utc)
    next_ts = loop_registry._compute_next_fire(meta, base=base)
    assert next_ts is not None
    delta_hours = (next_ts - base).total_seconds() / 3600
    # / next 5pm ET from 23:00 UTC is later than "now" by more than an hour
    assert delta_hours > 0


@pytest.mark.asyncio
async def test_record_fire_end_inserts_with_computed_next_fire():
    pool, conn = _mock_pool()
    await loop_registry.record_fire_end(
        pool, "macro_backfill", status="ok", duration_ms=1234, error=None,
    )
    # / one upsert call into loop_activity
    assert conn.execute.call_count == 1
    args = conn.execute.call_args
    # / macro_backfill is cron 9am ET, so args should include that cron_hour_et
    assert "INSERT INTO loop_activity" in args.args[0]


@pytest.mark.asyncio
async def test_record_fire_end_captures_error_truncated():
    pool, conn = _mock_pool()
    long_err = "x" * 900
    await loop_registry.record_fire_end(
        pool, "analyst", status="error", duration_ms=50, error=long_err,
    )
    args = conn.execute.call_args.args
    # / error param is position 7 (0=sql, 1-6=name/kind/cadence/cron/dur/status)
    err_arg = args[7]
    assert err_arg is not None and len(err_arg) <= 500


@pytest.mark.asyncio
async def test_describe_loops_returns_row_per_metadata_entry():
    pool, _conn = _mock_pool()
    out = await loop_registry.describe_loops(pool)
    assert len(out) == len(loop_registry.LOOP_METADATA)
    names = {r["name"] for r in out}
    assert "analyst" in names and "macro_backfill" in names


@pytest.mark.asyncio
async def test_describe_loops_with_null_pool_still_returns_metadata():
    out = await loop_registry.describe_loops(None)
    assert len(out) == len(loop_registry.LOOP_METADATA)
    for row in out:
        assert row["last_fire_ts"] is None
        assert row["next_fire_ts"] is not None  # / projected from metadata


@pytest.mark.asyncio
async def test_enqueue_trigger_unknown_service_returns_none():
    pool, _ = _mock_pool()
    result = await loop_registry.enqueue_trigger(pool, "not_a_real_service")
    assert result is None


@pytest.mark.asyncio
async def test_enqueue_trigger_creates_row():
    pool, conn = _mock_pool()
    conn.fetchval = AsyncMock(return_value=None)  # / no existing pending
    conn.fetchrow = AsyncMock(return_value={"id": 42})
    result = await loop_registry.enqueue_trigger(pool, "macro_backfill")
    assert result == 42


@pytest.mark.asyncio
async def test_enqueue_trigger_dedupes_pending():
    pool, conn = _mock_pool()
    conn.fetchval = AsyncMock(return_value=99)  # / existing pending row
    result = await loop_registry.enqueue_trigger(pool, "macro_backfill")
    assert result == 99
    # / should not call fetchrow to insert a new one
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_tracker_context_manager_records_start_and_end():
    pool, conn = _mock_pool()
    async with loop_registry.track(pool, "analyst"):
        pass
    # / one start upsert + one end upsert
    assert conn.execute.call_count == 2


@pytest.mark.asyncio
async def test_tracker_records_error_status_on_exception():
    pool, conn = _mock_pool()
    with pytest.raises(ValueError):
        async with loop_registry.track(pool, "analyst"):
            raise ValueError("boom")
    # / last execute call should carry status='error'
    end_call = conn.execute.call_args_list[-1]
    assert end_call.args[6] == "error"


@pytest.mark.asyncio
async def test_claim_pending_triggers_returns_pairs():
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[
        {"id": 1, "service": "macro_backfill"},
        {"id": 2, "service": "insider_backfill"},
    ])
    out = await loop_registry.claim_pending_triggers(pool)
    assert out == [(1, "macro_backfill"), (2, "insider_backfill")]


@pytest.mark.asyncio
async def test_record_fire_start_clears_last_error():
    # / regression: stale CancelledError from a previous cycle bled through when
    # / the next cycle fired; record_fire_start must NULL out last_error.
    pool, conn = _mock_pool()
    await loop_registry.record_fire_start(pool, "analyst")
    assert conn.execute.call_count == 1
    sql = conn.execute.call_args.args[0]
    assert "last_error = NULL" in sql
    assert "last_error" in sql and "NULL" in sql


@pytest.mark.asyncio
async def test_is_loop_running_true_when_status_running():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(return_value={"last_status": "running"})
    assert await loop_registry.is_loop_running(pool, "analyst") is True


@pytest.mark.asyncio
async def test_is_loop_running_false_when_status_ok():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(return_value={"last_status": "ok"})
    assert await loop_registry.is_loop_running(pool, "analyst") is False


@pytest.mark.asyncio
async def test_is_loop_running_false_when_no_row():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(return_value=None)
    assert await loop_registry.is_loop_running(pool, "analyst") is False


@pytest.mark.asyncio
async def test_is_loop_running_null_pool_returns_false():
    assert await loop_registry.is_loop_running(None, "analyst") is False


@pytest.mark.asyncio
async def test_upsert_service_state_writes_row():
    pool, conn = _mock_pool()
    await loop_registry.upsert_service_state(
        pool, "kronos_hf_load", "success", error=None, duration_ms=5200,
    )
    assert conn.execute.call_count == 1
    args = conn.execute.call_args.args
    assert args[1] == "kronos_hf_load"
    assert args[2] == 5200
    assert args[3] == "success"
    assert args[4] is None


@pytest.mark.asyncio
async def test_upsert_service_state_truncates_long_error():
    pool, conn = _mock_pool()
    await loop_registry.upsert_service_state(
        pool, "kronos_hf_load", "error", error="x" * 900,
    )
    args = conn.execute.call_args.args
    assert args[4] is not None and len(args[4]) <= 500


@pytest.mark.asyncio
async def test_fetch_service_state_returns_row_as_dict():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(return_value={
        "name": "kronos_hf_load", "last_status": "success",
        "last_error": None, "last_fire_ts": None,
        "last_duration_ms": 5000, "updated_at": None,
    })
    out = await loop_registry.fetch_service_state(pool, "kronos_hf_load")
    assert out is not None and out["last_status"] == "success"


@pytest.mark.asyncio
async def test_fetch_service_state_returns_none_when_missing():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(return_value=None)
    out = await loop_registry.fetch_service_state(pool, "nonexistent")
    assert out is None

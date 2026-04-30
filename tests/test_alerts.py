# / tests for dashboard alerts (chart price-cross alerts crud + state machine)

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.dashboard.alerts import (
    VALID_DIRECTIONS,
    VALID_STATUSES,
    _coerce_price,
    _row_to_alert,
    create_alert,
    delete_alert,
    get_alert,
    list_alerts,
    mark_checked,
    mark_fired,
    sanitize_direction,
    sanitize_status,
    update_alert,
    validate_label,
)


def _mock_pool():
    pool = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="DELETE 1")
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


def test_direction_and_status_enums_complete():
    assert {"above", "below"} == VALID_DIRECTIONS
    assert {"active", "fired", "disabled"} == VALID_STATUSES


def test_sanitize_direction_accepts_valid():
    assert sanitize_direction("above") == "above"
    assert sanitize_direction("  ABOVE  ") == "above"
    assert sanitize_direction("Below") == "below"


def test_sanitize_direction_rejects_invalid():
    assert sanitize_direction("sideways") is None
    assert sanitize_direction(None) is None
    assert sanitize_direction(42) is None


def test_sanitize_status_accepts_valid():
    assert sanitize_status("active") == "active"
    assert sanitize_status("  Fired  ") == "fired"
    assert sanitize_status("DISABLED") == "disabled"


def test_sanitize_status_rejects_invalid():
    assert sanitize_status("pending") is None
    assert sanitize_status(None) is None


def test_validate_label_strips_and_caps():
    assert validate_label("  hello  ") == "hello"
    assert validate_label("") is None
    assert validate_label("   ") is None
    # / >200 chars truncated
    long = "x" * 500
    assert len(validate_label(long)) == 200


def test_validate_label_non_string():
    assert validate_label(42) is None
    assert validate_label(None) is None


def test_coerce_price_accepts_positive():
    assert _coerce_price(100) == Decimal("100")
    assert _coerce_price(100.5) == Decimal("100.5")
    assert _coerce_price("50.25") == Decimal("50.25")


def test_coerce_price_rejects_zero_and_negative():
    assert _coerce_price(0) is None
    assert _coerce_price(-10) is None


def test_coerce_price_rejects_nan_and_inf():
    # / float inf/nan must be rejected via is_finite check
    assert _coerce_price(float("inf")) is None
    assert _coerce_price(float("nan")) is None


def test_coerce_price_rejects_bool():
    # / True is technically an int in python but we explicitly reject
    assert _coerce_price(True) is None
    assert _coerce_price(False) is None


def test_coerce_price_rejects_bad_strings():
    assert _coerce_price("not a number") is None
    assert _coerce_price(None) is None


def test_row_to_alert_shape():
    ts = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    row = {
        "id": 1, "symbol": "AAPL", "price": Decimal("180.50"),
        "direction": "above", "label": "breakout", "status": "active",
        "last_check": None, "fired_at": None, "created_at": ts,
    }
    out = _row_to_alert(row)
    assert out["id"] == 1
    assert out["symbol"] == "AAPL"
    # / price serialized as float
    assert out["price"] == 180.5
    assert out["direction"] == "above"
    assert "2026-04-19" in out["created_at"]


def test_row_to_alert_handles_none_price():
    row = {
        "id": 1, "symbol": "AAPL", "price": None,
        "direction": "above", "label": None, "status": "active",
        "last_check": None, "fired_at": None, "created_at": None,
    }
    out = _row_to_alert(row)
    assert out["price"] is None


@pytest.mark.asyncio
async def test_list_alerts_null_pool_returns_empty():
    assert await list_alerts(None) == []


@pytest.mark.asyncio
async def test_list_alerts_no_filter_fetches_all():
    pool, conn = _mock_pool()
    ts = datetime(2026, 4, 19, tzinfo=timezone.utc)
    conn.fetch = AsyncMock(return_value=[
        {"id": 1, "symbol": "AAPL", "price": Decimal("180"), "direction": "above",
         "label": None, "status": "active", "last_check": None, "fired_at": None,
         "created_at": ts},
    ])
    out = await list_alerts(pool)
    assert len(out) == 1
    sql = conn.fetch.call_args.args[0]
    # / no WHERE clause when no filters
    assert "WHERE" not in sql


@pytest.mark.asyncio
async def test_list_alerts_with_symbol_filter_builds_where():
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(return_value=[])
    await list_alerts(pool, symbol="AAPL", status="active")
    sql = conn.fetch.call_args.args[0]
    assert "WHERE" in sql
    assert "symbol = $1" in sql
    assert "status = $2" in sql


@pytest.mark.asyncio
async def test_list_alerts_db_error_returns_empty():
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(side_effect=Exception("dead"))
    out = await list_alerts(pool)
    assert out == []


@pytest.mark.asyncio
async def test_get_alert_returns_row():
    pool, conn = _mock_pool()
    ts = datetime(2026, 4, 19, tzinfo=timezone.utc)
    conn.fetchrow = AsyncMock(return_value={
        "id": 5, "symbol": "MSFT", "price": Decimal("300"),
        "direction": "below", "label": None, "status": "active",
        "last_check": None, "fired_at": None, "created_at": ts,
    })
    out = await get_alert(pool, 5)
    assert out["id"] == 5
    assert out["symbol"] == "MSFT"


@pytest.mark.asyncio
async def test_get_alert_missing_returns_none():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(return_value=None)
    assert await get_alert(pool, 999) is None


@pytest.mark.asyncio
async def test_get_alert_null_pool_returns_none():
    assert await get_alert(None, 1) is None


@pytest.mark.asyncio
async def test_create_alert_invalid_direction_short_circuits():
    pool, conn = _mock_pool()
    out = await create_alert(pool, "AAPL", 100, "sideways")
    assert out == {"error": "invalid_direction"}
    # / no db call made
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_create_alert_invalid_price_short_circuits():
    pool, conn = _mock_pool()
    out = await create_alert(pool, "AAPL", -10, "above")
    assert out == {"error": "invalid_price"}
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_create_alert_null_pool_returns_not_ready():
    out = await create_alert(None, "AAPL", 100, "above")
    assert out == {"error": "db_not_ready"}


@pytest.mark.asyncio
async def test_create_alert_happy_path():
    pool, conn = _mock_pool()
    ts = datetime(2026, 4, 19, tzinfo=timezone.utc)
    conn.fetchrow = AsyncMock(return_value={
        "id": 7, "symbol": "AAPL", "price": Decimal("180"),
        "direction": "above", "label": "breakout", "status": "active",
        "last_check": None, "fired_at": None, "created_at": ts,
    })
    out = await create_alert(pool, "AAPL", 180, "above", label="breakout")
    assert out["id"] == 7
    assert out["price"] == 180.0


@pytest.mark.asyncio
async def test_create_alert_db_error():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(side_effect=Exception("dead"))
    out = await create_alert(pool, "AAPL", 100, "above")
    assert out == {"error": "insert_failed"}


@pytest.mark.asyncio
async def test_update_alert_empty_patch_returns_none():
    pool, _conn = _mock_pool()
    # / no valid whitelisted fields -> no update
    out = await update_alert(pool, "AAPL", 1, unknown_field="x")
    assert out is None


@pytest.mark.asyncio
async def test_update_alert_invalid_status_returns_none():
    pool, _conn = _mock_pool()
    out = await update_alert(pool, "AAPL", 1, status="weird")
    assert out is None


@pytest.mark.asyncio
async def test_update_alert_invalid_price_returns_none():
    pool, _conn = _mock_pool()
    out = await update_alert(pool, "AAPL", 1, price=-5)
    assert out is None


@pytest.mark.asyncio
async def test_update_alert_null_pool():
    assert await update_alert(None, "AAPL", 1, status="disabled") is None


@pytest.mark.asyncio
async def test_update_alert_builds_scoped_sql():
    pool, conn = _mock_pool()
    ts = datetime(2026, 4, 19, tzinfo=timezone.utc)
    conn.fetchrow = AsyncMock(return_value={
        "id": 1, "symbol": "AAPL", "price": Decimal("180"),
        "direction": "above", "label": None, "status": "disabled",
        "last_check": None, "fired_at": None, "created_at": ts,
    })
    out = await update_alert(pool, "AAPL", 1, status="disabled")
    assert out is not None and out["status"] == "disabled"
    sql = conn.fetchrow.call_args.args[0]
    # / WHERE clause must be scoped to id AND symbol to prevent cross-symbol bleed
    assert "AND symbol" in sql


@pytest.mark.asyncio
async def test_delete_alert_success():
    pool, conn = _mock_pool()
    conn.execute = AsyncMock(return_value="DELETE 1")
    assert await delete_alert(pool, "AAPL", 1) is True


@pytest.mark.asyncio
async def test_delete_alert_miss():
    pool, conn = _mock_pool()
    conn.execute = AsyncMock(return_value="DELETE 0")
    assert await delete_alert(pool, "AAPL", 999) is False


@pytest.mark.asyncio
async def test_delete_alert_null_pool():
    assert await delete_alert(None, "AAPL", 1) is False


@pytest.mark.asyncio
async def test_delete_alert_db_error():
    pool, conn = _mock_pool()
    conn.execute = AsyncMock(side_effect=Exception("dead"))
    assert await delete_alert(pool, "AAPL", 1) is False


@pytest.mark.asyncio
async def test_mark_fired_atomic_check_and_set_where_active():
    # / state machine invariant: mark_fired only affects rows with status='active'
    pool, conn = _mock_pool()
    ts = datetime(2026, 4, 19, tzinfo=timezone.utc)
    conn.fetchrow = AsyncMock(return_value={
        "id": 1, "symbol": "AAPL", "price": Decimal("180"),
        "direction": "above", "label": None, "status": "fired",
        "last_check": None, "fired_at": ts, "created_at": ts,
    })
    out = await mark_fired(pool, 1, ts)
    assert out is not None and out["status"] == "fired"
    sql = conn.fetchrow.call_args.args[0]
    assert "status = 'active'" in sql  # / guards against double-fire


@pytest.mark.asyncio
async def test_mark_fired_already_fired_returns_none():
    # / race: second call when first already flipped status returns None
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(return_value=None)
    ts = datetime(2026, 4, 19, tzinfo=timezone.utc)
    assert await mark_fired(pool, 1, ts) is None


@pytest.mark.asyncio
async def test_mark_fired_null_pool():
    ts = datetime(2026, 4, 19, tzinfo=timezone.utc)
    assert await mark_fired(None, 1, ts) is None


@pytest.mark.asyncio
async def test_mark_checked_batch_noop_on_empty_list():
    pool, conn = _mock_pool()
    await mark_checked(pool, [], datetime.now(timezone.utc))
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_mark_checked_batches_into_single_statement():
    pool, conn = _mock_pool()
    ts = datetime(2026, 4, 19, tzinfo=timezone.utc)
    await mark_checked(pool, [1, 2, 3], ts)
    # / one SQL call regardless of count
    assert conn.execute.call_count == 1
    args = conn.execute.call_args.args
    assert args[1] == [1, 2, 3]


@pytest.mark.asyncio
async def test_mark_checked_null_pool_noop():
    # / should not raise
    await mark_checked(None, [1, 2], datetime.now(timezone.utc))

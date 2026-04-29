# / tests for dashboard drawings (user drawing persistence)

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.dashboard.drawings import (
    VALID_DRAWING_TYPES,
    _parse_jsonb,
    _row_to_drawing,
    create_drawing,
    delete_all_drawings,
    delete_drawing,
    list_drawings,
    sanitize_drawing_type,
    update_drawing,
    validate_payload,
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


def test_valid_drawing_types_contain_core_shapes():
    core = {"trendline", "horizontal_line", "vertical_line", "rectangle",
            "fib_retracement", "fib_extension", "text"}
    assert core.issubset(VALID_DRAWING_TYPES)


def test_sanitize_drawing_type_accepts_known():
    assert sanitize_drawing_type("trendline") == "trendline"
    assert sanitize_drawing_type("  Trendline  ") == "trendline"


def test_sanitize_drawing_type_rejects_unknown():
    assert sanitize_drawing_type("my_shape") is None


def test_sanitize_drawing_type_rejects_non_string():
    assert sanitize_drawing_type(42) is None
    assert sanitize_drawing_type(None) is None


def test_validate_payload_accepts_small_dict():
    assert validate_payload({"anchors": [[1, 100]], "style": {"color": "blue"}}) is True


def test_validate_payload_rejects_non_dict():
    assert validate_payload([1, 2]) is False
    assert validate_payload("string") is False
    assert validate_payload(None) is False


def test_validate_payload_rejects_oversized():
    # / >32kb -> reject
    oversized = {"data": "x" * (33 * 1024)}
    assert validate_payload(oversized) is False


def test_validate_payload_at_boundary():
    # / just under 32kb -> accept
    small = {"data": "x" * (30 * 1024)}
    assert validate_payload(small) is True


def test_validate_payload_rejects_unserializable():
    class Opaque:
        pass
    assert validate_payload({"obj": Opaque()}) is False


def test_parse_jsonb_passthrough_dict():
    assert _parse_jsonb({"a": 1}) == {"a": 1}


def test_parse_jsonb_passthrough_list():
    assert _parse_jsonb([1, 2]) == [1, 2]


def test_parse_jsonb_none_returns_none():
    assert _parse_jsonb(None) is None


def test_parse_jsonb_string_parses_valid():
    assert _parse_jsonb('{"a": 1}') == {"a": 1}


def test_parse_jsonb_string_returns_none_on_invalid():
    assert _parse_jsonb("not json") is None


def test_row_to_drawing_maps_all_fields():
    row = {
        "id": 42,
        "drawing_type": "trendline",
        "payload": '{"x": 1}',
        "created_at": datetime(2026, 4, 19, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 4, 20, tzinfo=timezone.utc),
    }
    out = _row_to_drawing(row)
    assert out["id"] == 42
    assert out["drawing_type"] == "trendline"
    assert out["payload"] == {"x": 1}
    assert "2026-04-19" in out["created_at"]


@pytest.mark.asyncio
async def test_list_drawings_returns_rows_as_dicts():
    pool, conn = _mock_pool()
    ts = datetime(2026, 4, 19, tzinfo=timezone.utc)
    conn.fetch = AsyncMock(return_value=[
        {"id": 1, "drawing_type": "trendline", "payload": {"x": 1},
         "created_at": ts, "updated_at": ts},
        {"id": 2, "drawing_type": "rectangle", "payload": {"y": 2},
         "created_at": ts, "updated_at": ts},
    ])
    out = await list_drawings(pool, "AAPL")
    assert len(out) == 2
    assert out[0]["drawing_type"] == "trendline"
    assert out[1]["drawing_type"] == "rectangle"


@pytest.mark.asyncio
async def test_list_drawings_db_error_returns_empty():
    pool, conn = _mock_pool()
    conn.fetch = AsyncMock(side_effect=Exception("db dead"))
    out = await list_drawings(pool, "AAPL")
    assert out == []


@pytest.mark.asyncio
async def test_create_drawing_returns_row_dict():
    pool, conn = _mock_pool()
    ts = datetime(2026, 4, 19, tzinfo=timezone.utc)
    conn.fetchrow = AsyncMock(return_value={
        "id": 5, "drawing_type": "trendline", "payload": {"anchors": [[1, 100]]},
        "created_at": ts, "updated_at": ts,
    })
    out = await create_drawing(pool, "AAPL", "trendline", {"anchors": [[1, 100]]})
    assert out["id"] == 5
    assert out["drawing_type"] == "trendline"


@pytest.mark.asyncio
async def test_create_drawing_db_error_returns_error_dict():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(side_effect=Exception("insert dead"))
    out = await create_drawing(pool, "AAPL", "trendline", {"x": 1})
    assert out == {"error": "insert_failed"}


@pytest.mark.asyncio
async def test_create_drawing_returns_error_on_none_row():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(return_value=None)
    out = await create_drawing(pool, "AAPL", "trendline", {"x": 1})
    assert out == {"error": "insert_failed"}


@pytest.mark.asyncio
async def test_update_drawing_scoped_to_symbol():
    # / verify sql filters on id AND symbol — pass in a symbol to the call
    pool, conn = _mock_pool()
    ts = datetime(2026, 4, 19, tzinfo=timezone.utc)
    conn.fetchrow = AsyncMock(return_value={
        "id": 1, "drawing_type": "rectangle", "payload": {"upd": True},
        "created_at": ts, "updated_at": ts,
    })
    out = await update_drawing(pool, "AAPL", 1, {"upd": True})
    assert out is not None and out["id"] == 1
    args = conn.fetchrow.call_args.args
    # / sql text should include id = $2 AND symbol = $3
    assert "AND symbol" in args[0]
    assert args[2] == 1
    assert args[3] == "AAPL"


@pytest.mark.asyncio
async def test_update_drawing_missing_row_returns_none():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(return_value=None)
    out = await update_drawing(pool, "AAPL", 999, {"x": 1})
    assert out is None


@pytest.mark.asyncio
async def test_update_drawing_db_error_returns_none():
    pool, conn = _mock_pool()
    conn.fetchrow = AsyncMock(side_effect=Exception("update dead"))
    out = await update_drawing(pool, "AAPL", 1, {"x": 1})
    assert out is None


@pytest.mark.asyncio
async def test_delete_drawing_success():
    pool, conn = _mock_pool()
    conn.execute = AsyncMock(return_value="DELETE 1")
    assert await delete_drawing(pool, "AAPL", 1) is True


@pytest.mark.asyncio
async def test_delete_drawing_miss_returns_false():
    pool, conn = _mock_pool()
    conn.execute = AsyncMock(return_value="DELETE 0")
    assert await delete_drawing(pool, "AAPL", 999) is False


@pytest.mark.asyncio
async def test_delete_drawing_db_error_returns_false():
    pool, conn = _mock_pool()
    conn.execute = AsyncMock(side_effect=Exception("delete dead"))
    assert await delete_drawing(pool, "AAPL", 1) is False


@pytest.mark.asyncio
async def test_delete_drawing_unexpected_result_format():
    pool, conn = _mock_pool()
    # / non-string result
    conn.execute = AsyncMock(return_value=None)
    assert await delete_drawing(pool, "AAPL", 1) is False


@pytest.mark.asyncio
async def test_delete_all_drawings_returns_deleted_count():
    pool, conn = _mock_pool()
    conn.execute = AsyncMock(return_value="DELETE 7")
    assert await delete_all_drawings(pool, "AAPL") == 7


@pytest.mark.asyncio
async def test_delete_all_drawings_db_error_returns_zero():
    pool, conn = _mock_pool()
    conn.execute = AsyncMock(side_effect=Exception("nope"))
    assert await delete_all_drawings(pool, "AAPL") == 0


@pytest.mark.asyncio
async def test_delete_all_drawings_malformed_result_returns_zero():
    pool, conn = _mock_pool()
    conn.execute = AsyncMock(return_value="WEIRD")
    assert await delete_all_drawings(pool, "AAPL") == 0

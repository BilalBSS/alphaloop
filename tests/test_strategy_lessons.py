# / tests for StrategyLessons — per-strategy lesson accumulation

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.knowledge.strategy_lessons import (
    VALID_LESSON_TYPES,
    StrategyLessons,
    _infer_confidence,
)


def _mock_pool(fetchrow_return=None):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


def _stub_writer(existing_doc: str | None = "# stub\n"):
    # / writer with read_document + write_document + append_section stubbed
    w = MagicMock()
    w.read_document = AsyncMock(return_value=existing_doc)
    w.write_document = AsyncMock(return_value="strategies/sid_1.md")
    w.append_section = AsyncMock(return_value=None)
    return w


# ──────────────────────────────────────────────────────
# _infer_confidence — pure function, exact boundary tests
# ──────────────────────────────────────────────────────

class TestInferConfidence:
    def test_none_is_anecdotal(self):
        assert _infer_confidence(None) == "anecdotal"

    def test_zero_is_anecdotal(self):
        assert _infer_confidence(0) == "anecdotal"

    def test_below_10_is_anecdotal(self):
        assert _infer_confidence(9) == "anecdotal"

    def test_boundary_10_becomes_emerging(self):
        # / exact boundary: < 10 is anecdotal, 10 is emerging
        assert _infer_confidence(10) == "emerging"

    def test_29_still_emerging(self):
        assert _infer_confidence(29) == "emerging"

    def test_boundary_30_becomes_established(self):
        assert _infer_confidence(30) == "established"

    def test_99_still_established(self):
        assert _infer_confidence(99) == "established"

    def test_boundary_100_becomes_canonical(self):
        assert _infer_confidence(100) == "canonical"

    def test_large_count_still_canonical(self):
        assert _infer_confidence(10_000) == "canonical"


# ──────────────────────────────────────────────────────
# VALID_LESSON_TYPES contract
# ──────────────────────────────────────────────────────

class TestValidLessonTypes:
    def test_core_lesson_types_present(self):
        expected = {
            "mutation_result", "killed", "promotion", "decay_detected",
            "regime_performance", "dormant_detected", "activation",
        }
        assert expected.issubset(VALID_LESSON_TYPES)


# ──────────────────────────────────────────────────────
# StrategyLessons.record
# ──────────────────────────────────────────────────────

class TestRecord:
    @pytest.mark.asyncio
    async def test_invalid_lesson_type_raises(self):
        pool, _ = _mock_pool()
        sl = StrategyLessons(pool=pool, writer=_stub_writer())
        with pytest.raises(ValueError, match="invalid lesson_type"):
            await sl.record("sid_1", "bogus_type", "content")

    @pytest.mark.asyncio
    async def test_returns_inserted_id(self):
        pool, _ = _mock_pool(fetchrow_return={"id": 42})
        sl = StrategyLessons(pool=pool, writer=_stub_writer())
        result = await sl.record(
            strategy_id="sid_1",
            lesson_type="killed",
            content="strategy underperformed sector avg",
        )
        assert result == 42
        assert isinstance(result, int)

    @pytest.mark.asyncio
    async def test_insert_sql_shape(self):
        pool, conn = _mock_pool(fetchrow_return={"id": 1})
        sl = StrategyLessons(pool=pool, writer=_stub_writer())
        await sl.record("sid_1", "promotion", "promoted to active")
        sql = conn.fetchrow.await_args.args[0]
        assert "INSERT INTO strategy_lessons" in sql
        assert "RETURNING id" in sql

    @pytest.mark.asyncio
    async def test_context_serialized_as_json(self):
        pool, conn = _mock_pool(fetchrow_return={"id": 1})
        sl = StrategyLessons(pool=pool, writer=_stub_writer())
        ctx = {"sharpe": 1.2, "regime": "bull"}
        await sl.record("sid_1", "mutation_result", "ok", context=ctx)
        args = conn.fetchrow.await_args.args
        # / context is stringified JSON
        json_blob = [a for a in args if isinstance(a, str) and a.startswith("{")]
        assert len(json_blob) >= 1
        assert json.loads(json_blob[0]) == ctx

    @pytest.mark.asyncio
    async def test_none_context_stored_as_none(self):
        pool, conn = _mock_pool(fetchrow_return={"id": 1})
        sl = StrategyLessons(pool=pool, writer=_stub_writer())
        await sl.record("sid_1", "killed", "content", context=None)
        args = conn.fetchrow.await_args.args
        # / context position is $4; check None passed through
        assert None in args

    @pytest.mark.asyncio
    async def test_trade_count_drives_confidence(self):
        pool, conn = _mock_pool(fetchrow_return={"id": 1})
        sl = StrategyLessons(pool=pool, writer=_stub_writer())
        await sl.record(
            "sid_1", "promotion", "content", trade_count=50,
        )
        # / trade_count=50 -> established
        args = conn.fetchrow.await_args.args
        assert "established" in args
        assert 50 in args

    @pytest.mark.asyncio
    async def test_appends_to_existing_playbook_no_seed(self):
        pool, _ = _mock_pool(fetchrow_return={"id": 1})
        writer = _stub_writer(existing_doc="# existing stub\n")
        sl = StrategyLessons(pool=pool, writer=writer)
        await sl.record("sid_1", "killed", "the reason")
        # / existing file -> skip write_document seed, go straight to append
        writer.write_document.assert_not_called()
        writer.append_section.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_seeds_stub_playbook_when_missing(self):
        pool, _ = _mock_pool(fetchrow_return={"id": 1})
        writer = _stub_writer(existing_doc=None)
        sl = StrategyLessons(pool=pool, writer=writer)
        await sl.record("sid_1", "activation", "now live")
        # / missing -> stub written first
        writer.write_document.assert_awaited_once()
        kwargs = writer.write_document.await_args.kwargs
        assert kwargs["category"] == "strategies"
        assert kwargs["filename"] == "sid_1.md"
        assert kwargs["strategy_ids"] == ["sid_1"]
        # / then appended
        writer.append_section.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_append_heading_includes_type_and_confidence(self):
        pool, _ = _mock_pool(fetchrow_return={"id": 1})
        writer = _stub_writer(existing_doc="# stub")
        sl = StrategyLessons(pool=pool, writer=writer)
        await sl.record(
            "sid_1", "decay_detected", "sharpe dropped 30%",
            trade_count=150,
        )
        call = writer.append_section.await_args
        heading = call.kwargs["heading"]
        # / heading includes lesson type and confidence tier
        assert "decay_detected" in heading
        assert "canonical" in heading  # / 150 trades -> canonical


# ──────────────────────────────────────────────────────
# StrategyLessons.get_context
# ──────────────────────────────────────────────────────

class TestGetContext:
    @pytest.mark.asyncio
    async def test_returns_rows_as_dicts(self):
        rows = [
            {"lesson_type": "killed", "content": "bad",
             "confidence": "anecdotal", "trade_count": 5,
             "created_at": None},
            {"lesson_type": "mutation_result", "content": "good",
             "confidence": "emerging", "trade_count": 20,
             "created_at": None},
        ]
        pool, conn = _mock_pool()
        conn.fetch = AsyncMock(return_value=rows)
        sl = StrategyLessons(pool=pool, writer=_stub_writer())
        result = await sl.get_context("sid_1")
        assert len(result) == 2
        assert result[0]["lesson_type"] == "killed"
        assert isinstance(result[0], dict)

    @pytest.mark.asyncio
    async def test_empty_returns_empty_list(self):
        pool, conn = _mock_pool()
        conn.fetch = AsyncMock(return_value=[])
        sl = StrategyLessons(pool=pool, writer=_stub_writer())
        assert await sl.get_context("sid_1") == []

    @pytest.mark.asyncio
    async def test_default_limit_is_10(self):
        pool, conn = _mock_pool()
        conn.fetch = AsyncMock(return_value=[])
        sl = StrategyLessons(pool=pool, writer=_stub_writer())
        await sl.get_context("sid_1")
        args = conn.fetch.await_args.args
        assert args[-1] == 10

    @pytest.mark.asyncio
    async def test_custom_limit_forwarded(self):
        pool, conn = _mock_pool()
        conn.fetch = AsyncMock(return_value=[])
        sl = StrategyLessons(pool=pool, writer=_stub_writer())
        await sl.get_context("sid_1", limit=25)
        args = conn.fetch.await_args.args
        assert args[-1] == 25

    @pytest.mark.asyncio
    async def test_strategy_id_passed_as_first_filter(self):
        pool, conn = _mock_pool()
        conn.fetch = AsyncMock(return_value=[])
        sl = StrategyLessons(pool=pool, writer=_stub_writer())
        await sl.get_context("sid_special")
        args = conn.fetch.await_args.args
        sql = args[0]
        assert "WHERE strategy_id = $1" in sql
        assert "ORDER BY created_at DESC" in sql
        assert args[1] == "sid_special"

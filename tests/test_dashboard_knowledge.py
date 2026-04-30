# / tests for phase 2 knowledge endpoints — wiki + post-mortems + regime-shifts

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


def _record(d: dict) -> MagicMock:
    # / mimic asyncpg.Record behavior for _serialize() iteration
    m = MagicMock()
    m.items.return_value = list(d.items())
    m.keys.return_value = list(d.keys())
    m.__iter__ = lambda s: iter(d.items())
    # / subscript access for endpoint code that does row["key"]
    m.__getitem__.side_effect = lambda k: d[k]
    return m


def _mock_pool(rows=None, row=None):
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [_record(r) for r in (rows or [])]
    mock_conn.fetchrow.return_value = _record(row) if row is not None else None
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_conn
    mock_ctx.__aexit__.return_value = False
    pool = MagicMock()
    pool.acquire.return_value = mock_ctx
    return pool, mock_conn


async def _get(url: str):
    from src.dashboard import app as dashboard
    async with AsyncClient(transport=ASGITransport(app=dashboard.app), base_url="http://t") as c:
        return await c.get(url)


# ───────────────────────────────────────────
# /api/wiki/documents
# ───────────────────────────────────────────

class TestWikiDocumentsList:
    @pytest.mark.asyncio
    async def test_no_filters_returns_all(self):
        from src.dashboard import app as dashboard
        rows = [
            {"id": 1, "path": "regimes/bull.md", "category": "regimes",
             "title": "Bull", "symbols": [], "strategy_ids": [],
             "word_count": 80, "confidence": "emerging",
             "created_at": datetime(2026, 4, 17), "updated_at": datetime(2026, 4, 17)},
        ]
        pool, conn = _mock_pool(rows=rows)
        dashboard.STATE.pool = pool
        resp = await _get("/api/wiki/documents")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["path"] == "regimes/bull.md"
        # / no WHERE clause when no filter — only the limit param
        sql = conn.fetch.await_args.args[0]
        assert "WHERE" not in sql
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_category_filter_injected(self):
        from src.dashboard import app as dashboard
        pool, conn = _mock_pool(rows=[])
        dashboard.STATE.pool = pool
        resp = await _get("/api/wiki/documents?category=strategies")
        assert resp.status_code == 200
        sql = conn.fetch.await_args.args[0]
        assert "category = $1" in sql
        assert conn.fetch.await_args.args[1] == "strategies"
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_invalid_category_400(self):
        from src.dashboard import app as dashboard
        pool, _ = _mock_pool(rows=[])
        dashboard.STATE.pool = pool
        resp = await _get("/api/wiki/documents?category=bogus")
        assert resp.status_code == 400
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_symbol_filter_uppercases(self):
        from src.dashboard import app as dashboard
        pool, conn = _mock_pool(rows=[])
        dashboard.STATE.pool = pool
        resp = await _get("/api/wiki/documents?symbol=aapl")
        assert resp.status_code == 200
        # / symbol is uppercased by the endpoint before binding
        args = conn.fetch.await_args.args
        assert "AAPL" in args
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_limit_clamped(self):
        from src.dashboard import app as dashboard
        pool, conn = _mock_pool(rows=[])
        dashboard.STATE.pool = pool
        resp = await _get("/api/wiki/documents?limit=99999")
        assert resp.status_code == 200
        # / last arg is the limit, clamped to 500 per endpoint
        args = conn.fetch.await_args.args
        assert args[-1] == 500
        dashboard.STATE.pool = None


# ───────────────────────────────────────────
# /api/wiki/document
# ───────────────────────────────────────────

class TestWikiDocumentRead:
    @pytest.mark.asyncio
    async def test_happy_path_returns_content(self):
        from src.dashboard import app as dashboard
        pool, _ = _mock_pool(row={"path": "regimes/bull.md", "category": "regimes", "title": "Bull"})
        dashboard.STATE.pool = pool

        with patch("src.knowledge.wiki_writer.WikiWriter") as m_cls:
            instance = MagicMock()
            instance.read_document = AsyncMock(return_value="# Bull\n\nbody text")
            m_cls.return_value = instance
            resp = await _get("/api/wiki/document?path=regimes/bull.md")

        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "regimes/bull.md"
        assert data["title"] == "Bull"
        assert "body text" in data["content"]
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self):
        from src.dashboard import app as dashboard
        pool, _ = _mock_pool()
        dashboard.STATE.pool = pool
        resp = await _get("/api/wiki/document?path=../../../etc/passwd")
        assert resp.status_code == 400
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_absolute_path_blocked(self):
        from src.dashboard import app as dashboard
        pool, _ = _mock_pool()
        dashboard.STATE.pool = pool
        resp = await _get("/api/wiki/document?path=/etc/passwd")
        assert resp.status_code == 400
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_missing_row_returns_404(self):
        from src.dashboard import app as dashboard
        pool, _ = _mock_pool(row=None)
        dashboard.STATE.pool = pool
        resp = await _get("/api/wiki/document?path=regimes/ghost.md")
        assert resp.status_code == 404
        dashboard.STATE.pool = None


# ───────────────────────────────────────────
# /api/post-mortems
# ───────────────────────────────────────────

class TestPostMortemsList:
    @pytest.mark.asyncio
    async def test_returns_rows(self):
        from src.dashboard import app as dashboard
        rows = [{
            "id": 1, "strategy_id": "s1", "symbol": "AAPL", "trigger_type": "loss_threshold",
            "pnl": Decimal("-60.00"), "expected_pnl": None, "deviation_sigma": Decimal("1.5"),
            "details": {"model_used": "llama-3.3-70b"}, "wiki_path": "post-mortems/s1_aapl_2026-04-17.md",
            "created_at": datetime(2026, 4, 17),
        }]
        pool, _conn = _mock_pool(rows=rows)
        dashboard.STATE.pool = pool
        resp = await _get("/api/post-mortems")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["strategy_id"] == "s1"
        assert data[0]["symbol"] == "AAPL"
        # / jsonb details preserved as dict
        assert data[0]["details"]["model_used"] == "llama-3.3-70b"
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_strategy_filter_uses_where(self):
        from src.dashboard import app as dashboard
        pool, conn = _mock_pool(rows=[])
        dashboard.STATE.pool = pool
        resp = await _get("/api/post-mortems?strategy_id=s1&limit=25")
        assert resp.status_code == 200
        sql = conn.fetch.await_args.args[0]
        assert "WHERE strategy_id" in sql
        args = conn.fetch.await_args.args
        assert "s1" in args
        assert 25 in args
        dashboard.STATE.pool = None


# ───────────────────────────────────────────
# /api/regime-shifts
# ───────────────────────────────────────────

class TestRegimeShiftsList:
    @pytest.mark.asyncio
    async def test_returns_rows(self):
        from src.dashboard import app as dashboard
        rows = [{
            "id": 1, "old_regime": "bull", "new_regime": "bear", "market": "equity",
            "confidence": Decimal("0.85"), "wiki_path": "regimes/bear_2026-04-17.md",
            "detected_at": datetime(2026, 4, 17),
        }]
        pool, _conn = _mock_pool(rows=rows)
        dashboard.STATE.pool = pool
        resp = await _get("/api/regime-shifts")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["old_regime"] == "bull"
        assert data[0]["new_regime"] == "bear"
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_market_filter(self):
        from src.dashboard import app as dashboard
        pool, conn = _mock_pool(rows=[])
        dashboard.STATE.pool = pool
        resp = await _get("/api/regime-shifts?market=crypto")
        assert resp.status_code == 200
        args = conn.fetch.await_args.args
        assert "crypto" in args
        dashboard.STATE.pool = None

    @pytest.mark.asyncio
    async def test_invalid_market_400(self):
        from src.dashboard import app as dashboard
        pool, _ = _mock_pool(rows=[])
        dashboard.STATE.pool = pool
        resp = await _get("/api/regime-shifts?market=bogus")
        assert resp.status_code == 400
        dashboard.STATE.pool = None

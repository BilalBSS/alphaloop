from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


def _stub_pool(flags=None):
    flags = dict(flags or {})
    pool = MagicMock()
    conn = AsyncMock()

    async def fetchrow(query, *args):
        if "SELECT value FROM system_flags" in query:
            key = args[0]
            if key in flags:
                return {"value": flags[key]}
            return None
        return None

    async def execute(query, *args):
        if "INSERT INTO system_flags" in query:
            flags[args[0]] = args[1]
        return None

    conn.fetchrow.side_effect = fetchrow
    conn.execute.side_effect = execute
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool, flags


def test_admin_pause_get_returns_current_state(monkeypatch):
    from src.dashboard import state as state_module
    pool, _ = _stub_pool({"executor_paused": "true"})
    monkeypatch.setattr(state_module.STATE, "pool", pool)

    from src.dashboard.app import app
    client = TestClient(app)
    resp = client.get("/api/admin/pause")
    assert resp.status_code == 200
    assert resp.json() == {"paused": True}


def test_admin_pause_post_requires_token(monkeypatch):
    from src.dashboard import state as state_module
    pool, _ = _stub_pool()
    monkeypatch.setattr(state_module.STATE, "pool", pool)
    monkeypatch.setattr(state_module.STATE, "admin_token", "x" * 32)

    from src.dashboard.app import app
    client = TestClient(app)
    resp = client.post("/api/admin/pause", json={"paused": True})
    assert resp.status_code == 401


def test_admin_pause_post_503_when_unset(monkeypatch):
    from src.dashboard import state as state_module
    pool, _ = _stub_pool()
    monkeypatch.setattr(state_module.STATE, "pool", pool)
    monkeypatch.setattr(state_module.STATE, "admin_token", None)

    from src.dashboard.app import app
    client = TestClient(app)
    resp = client.post("/api/admin/pause", json={"paused": True})
    assert resp.status_code == 503


def test_admin_pause_post_toggles(monkeypatch):
    from src.dashboard import state as state_module
    pool, flags = _stub_pool()
    monkeypatch.setattr(state_module.STATE, "pool", pool)
    token = "y" * 32
    monkeypatch.setattr(state_module.STATE, "admin_token", token)

    from src.dashboard.app import app
    client = TestClient(app)
    resp = client.post(
        "/api/admin/pause",
        json={"paused": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"paused": True}
    assert flags["executor_paused"] == "true"


@pytest.mark.asyncio
async def test_executor_skips_when_paused():
    from unittest.mock import patch

    from src.agents.executor_agent import ExecutorAgent

    agent = ExecutorAgent()
    pool = MagicMock()
    trade = {"symbol": "AAPL", "side": "buy", "qty": 10, "status": "pending"}

    with patch(
        "src.agents.system_flags.is_executor_paused",
        new=AsyncMock(return_value=True),
    ):
        result = await agent._preflight(pool, 1, trade, None)

    assert result == {"status": "paused", "reason": "executor_paused"}

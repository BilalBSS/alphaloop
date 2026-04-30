# / tests for request audit log middleware

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from src.dashboard.app import app
from src.dashboard.state import STATE


def _patch_audit():
    return patch("src.agents.data_tools.log_event", new_callable=AsyncMock)


def test_get_success_skips_audit():
    STATE.pool = MagicMock()
    with _patch_audit() as log_event:
        client = TestClient(app)
        client.get("/api/version")
        assert log_event.call_count == 0


def test_post_logs_audit():
    STATE.pool = MagicMock()
    with _patch_audit() as log_event:
        client = TestClient(app)
        client.post("/api/nonexistent-route")
        assert log_event.call_count == 1
        kwargs = log_event.call_args
        assert "POST" in kwargs[0][3]
        assert "/api/nonexistent-route" in kwargs[0][3]


def test_failed_get_logs_audit():
    STATE.pool = MagicMock()
    with _patch_audit() as log_event:
        client = TestClient(app)
        client.get("/api/nonexistent-route")
        assert log_event.call_count == 1


def test_skipped_path_no_audit():
    STATE.pool = MagicMock()
    with _patch_audit() as log_event:
        client = TestClient(app)
        client.get("/api/version")
        assert log_event.call_count == 0


def test_skip_paths_includes_health_and_version():
    from src.dashboard.app import _AUDIT_SKIP_PATHS
    assert "/api/health" in _AUDIT_SKIP_PATHS
    assert "/api/version" in _AUDIT_SKIP_PATHS


def test_no_pool_skips_audit():
    STATE.pool = None
    with _patch_audit() as log_event:
        client = TestClient(app)
        client.post("/api/nonexistent-route")
        assert log_event.call_count == 0

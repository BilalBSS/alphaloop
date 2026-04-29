# / tests for response cache headers on hot endpoints

from __future__ import annotations

from fastapi.testclient import TestClient

from src.dashboard.app import _CACHE_TTL_SECONDS, app


def test_version_has_cache_header():
    client = TestClient(app)
    resp = client.get("/api/version")
    assert resp.headers.get("Cache-Control") == f"max-age={_CACHE_TTL_SECONDS['/api/version']}"


def test_unknown_path_has_no_cache_header():
    client = TestClient(app)
    resp = client.get("/api/version")
    other = client.get("/api/unknown-route-xyz")
    assert "max-age" not in (other.headers.get("Cache-Control") or "")


def test_post_does_not_get_cache_header():
    client = TestClient(app)
    resp = client.post("/api/version")
    assert "max-age" not in (resp.headers.get("Cache-Control") or "")

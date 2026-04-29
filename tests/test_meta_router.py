# / tests for /api/version endpoint

from __future__ import annotations

from fastapi.testclient import TestClient

from src.dashboard.app import app


def test_version_returns_version_string():
    client = TestClient(app)
    resp = client.get("/api/version")
    assert resp.status_code == 200
    body = resp.json()
    assert "version" in body
    assert isinstance(body["version"], str)
    assert body["version"]


def test_version_matches_version_file():
    from pathlib import Path
    expected = (Path(__file__).parent.parent / "VERSION").read_text().strip()
    client = TestClient(app)
    resp = client.get("/api/version")
    assert resp.json()["version"] == expected

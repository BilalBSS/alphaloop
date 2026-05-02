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


def test_version_includes_git_sha_and_deployed_at():
    client = TestClient(app)
    resp = client.get("/api/version")
    body = resp.json()
    assert "git_sha" in body
    assert isinstance(body["git_sha"], str)
    assert "deployed_at" in body
    assert isinstance(body["deployed_at"], (int, float))
    assert "broker_mode" in body
    assert body["broker_mode"] in {"paper", "live"}


def test_version_uses_build_sha_env(monkeypatch):
    monkeypatch.setenv("BUILD_SHA", "abc123def4567890")
    from importlib import reload

    import src.dashboard.routers.meta as meta_module
    reload(meta_module)
    assert meta_module._GIT_SHA == "abc123def456"

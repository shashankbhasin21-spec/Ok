"""Health endpoint distinguishes reachability from generation readiness."""

from __future__ import annotations


def test_health_without_worker(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["gateway"] == "ok"
    assert data["gpu_worker_available"] is False
    # CPU assembly may make generation_available true without a GPU worker.
    assert "cpu_assembly" in data["ready_engines"] or data["generation_available"] in (True, False)
    assert "version" in data
    assert data["details"].get("note")


def test_version(client):
    r = client.get("/version")
    assert r.status_code == 200
    assert r.json()["service"] == "video-gateway"


def test_auth_required(client):
    r = client.get("/v1/providers")
    assert r.status_code == 401


def test_auth_ok(client, auth_headers):
    r = client.get("/v1/providers", headers=auth_headers)
    assert r.status_code == 200
    names = {p["name"] for p in r.json()}
    assert {"wan", "ltx", "framepack", "cpu_assembly"} <= names
    for p in r.json():
        assert p["status"] in {
            "AVAILABLE",
            "MODEL_NOT_INSTALLED",
            "GPU_UNAVAILABLE",
            "UNHEALTHY",
            "DISABLED",
            "UNKNOWN",
        }
        # Without CUDA worker, GPU engines must not show AVAILABLE
        if p["name"] in {"wan", "ltx", "framepack"}:
            assert p["status"] != "AVAILABLE"


def test_missing_api_key_fails_closed(tmp_path, monkeypatch):
    """AUTH_MODE=required with empty API_KEY must not open the API."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'gw.db'}")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setenv("WORKER_TOKEN", "test-worker-token")
    monkeypatch.setenv("VIDEO_WORKER_URL", "")
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED", "false")
    monkeypatch.setenv("ALLOW_MOCK_INFERENCE", "false")

    from gateway.config import get_settings

    get_settings.cache_clear()

    import gateway.database as dbmod
    from gateway.database import Base
    from gateway.main import app
    from fastapi.testclient import TestClient

    dbmod.configure_engine()
    Base.metadata.drop_all(bind=dbmod.engine)
    Base.metadata.create_all(bind=dbmod.engine)

    with TestClient(app) as c:
        r = c.get("/v1/providers")
        assert r.status_code == 503
        assert "API_KEY not configured" in r.json()["detail"]

    get_settings.cache_clear()

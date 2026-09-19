"""Health endpoint must never claim generation without a ready GPU worker."""

from __future__ import annotations


def test_health_without_worker(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["gateway"] == "ok"
    assert data["generation_available"] is False
    assert data["gpu_worker_available"] is False
    assert data["ready_engines"] == []
    assert "version" in data


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
    assert names == {"wan", "ltx", "framepack"}
    for p in r.json():
        assert p["status"] in {
            "AVAILABLE",
            "MODEL_NOT_INSTALLED",
            "GPU_UNAVAILABLE",
            "UNHEALTHY",
            "DISABLED",
            "UNKNOWN",
        }
        # Without CUDA worker, must not show AVAILABLE
        assert p["status"] != "AVAILABLE"

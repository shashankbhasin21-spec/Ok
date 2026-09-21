"""Temporary GPU readiness probe and readiness-gated test render."""

from __future__ import annotations

from gateway.config import get_settings
from gateway.models import VideoJob


def _fake_worker_health(*, cuda: bool, ready: list[str]):
    return {
        "ok": True,
        "generation_available": bool(cuda and ready),
        "cuda_available": cuda,
        "ready_engines": list(ready),
        "installed_engines": list(ready),
        "model_loading": {"wan": False, "ltx": False, "framepack": False},
        "vram_total_mb": 22591.9 if cuda else None,
        "vram_free_mb": 22397.9 if cuda else None,
    }


def _set_health(client, *, cuda: bool, ready: list[str]):
    health = _fake_worker_health(cuda=cuda, ready=ready)
    client.app.state.worker_client.health = lambda: health
    client.app.state.job_service.worker.health = lambda: health


def test_gpu_readiness_requires_auth(client):
    r = client.get("/internal/gpu-readiness")
    assert r.status_code == 401


def test_gpu_readiness_test_render_requires_auth(client):
    r = client.post("/internal/gpu-readiness-test-render")
    assert r.status_code == 401


def test_gpu_readiness_sanitized_fields(client, auth_headers):
    _set_health(client, cuda=True, ready=["ltx"])
    r = client.get("/internal/gpu-readiness", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "cuda_available",
        "ready_engines",
        "model_loading",
        "vram_total_mb",
        "vram_free_mb",
    }
    assert body["cuda_available"] is True
    assert body["ready_engines"] == ["ltx"]
    blob = str(body).lower()
    assert "api_key" not in blob
    assert "worker_token" not in blob
    assert "video_worker_url" not in blob


def test_test_render_blocked_when_cuda_false(client, auth_headers):
    _set_health(client, cuda=False, ready=["ltx"])
    r = client.post("/internal/gpu-readiness-test-render", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["submitted"] is False
    assert body["job_id"] is None
    assert body["status"] == "not_submitted"
    assert body["cuda_available"] is False
    assert client.app.state.job_service is not None
    # No jobs persisted
    from gateway.database import SessionLocal

    with SessionLocal() as db:
        assert db.query(VideoJob).count() == 0


def test_test_render_blocked_when_no_ready_engines(client, auth_headers):
    _set_health(client, cuda=True, ready=[])
    r = client.post("/internal/gpu-readiness-test-render", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["submitted"] is False
    assert body["job_id"] is None
    assert body["status"] == "not_submitted"
    assert body["cuda_available"] is True
    assert body["ready_engines"] == []
    from gateway.database import SessionLocal

    with SessionLocal() as db:
        assert db.query(VideoJob).count() == 0


def test_test_render_submits_one_vertical_30s_job(client, auth_headers):
    _set_health(client, cuda=True, ready=["ltx"])
    r = client.post("/internal/gpu-readiness-test-render", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["submitted"] is True
    assert body["job_id"]
    assert body["status"] == "QUEUED"
    assert body["cuda_available"] is True
    assert body["ready_engines"] == ["ltx"]

    from gateway.database import SessionLocal

    with SessionLocal() as db:
        jobs = db.query(VideoJob).all()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.id == body["job_id"]
        assert job.idempotency_key == "internal-gpu-readiness-test-render-v1"
        assert float(job.duration) == 30.0
        assert job.aspect_ratio == "9:16"
        assert "AI compute core" in (job.prompt or "")
        req = job.request_json or {}
        assert float(req.get("duration", 0)) == 30.0
        assert req.get("aspect_ratio") == "9:16"
        assert req.get("engine") == "auto"

    blob = str(body).lower()
    assert "api_key" not in blob
    assert "worker_token" not in blob
    assert "video_worker_url" not in blob
    assert "test-worker-token" not in blob


def test_test_render_idempotent(client, auth_headers):
    _set_health(client, cuda=True, ready=["ltx"])
    r1 = client.post("/internal/gpu-readiness-test-render", headers=auth_headers)
    r2 = client.post("/internal/gpu-readiness-test-render", headers=auth_headers)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["job_id"] == r2.json()["job_id"]
    assert r1.json()["submitted"] is True
    assert r2.json()["submitted"] is True

    from gateway.database import SessionLocal

    with SessionLocal() as db:
        assert db.query(VideoJob).count() == 1


def test_mock_inference_remains_disabled(client, auth_headers, monkeypatch):
    import os

    assert os.environ.get("ALLOW_MOCK_INFERENCE") == "false"
    _set_health(client, cuda=True, ready=["ltx"])
    client.post("/internal/gpu-readiness-test-render", headers=auth_headers)
    assert os.environ.get("ALLOW_MOCK_INFERENCE") == "false"
    # Gateway must not flip the worker mock flag as a side effect.
    assert "allow_mock_inference" not in get_settings().model_dump()

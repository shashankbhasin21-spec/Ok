"""Worker failure paths, readiness gating, CPU assembly, download, QC."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from gateway.models import VideoJob
from gateway.worker_client import WorkerGenerateError


def _wait_terminal(client, auth_headers, job_id, timeout=90.0):
    final = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        g = client.get(f"/v1/videos/{job_id}", headers=auth_headers)
        assert g.status_code == 200
        final = g.json()
        if final["status"] in ("FAILED", "COMPLETED", "CANCELLED"):
            return final
        time.sleep(0.15)
    return final


def _set_worker_health(client, health: dict):
    client.app.state.worker_client.health = lambda: health
    client.app.state.job_service.worker.health = lambda: health


def test_worker_reachable_but_engine_not_ready_uses_cpu_or_fails_clean(client, auth_headers):
    """Worker HTTP ok without ready GPU engines must not route to LTX."""
    _set_worker_health(
        client,
        {
            "ok": True,
            "worker_available": True,
            "generation_available": False,
            "cuda_available": False,
            "ready_engines": [],
            "installed_engines": ["ltx"],
            "model_loading": {},
            "vram_total_mb": None,
            "vram_free_mb": None,
        },
    )
    r = client.post(
        "/v1/videos",
        headers=auth_headers,
        json={
            "prompt": "Abstract compute core light",
            "duration": 2,
            "allow_short": True,
            "aspect_ratio": "9:16",
            "engine": "auto",
            "idempotency_key": "test-reachable-not-ready",
        },
    )
    assert r.status_code == 200
    job = _wait_terminal(client, auth_headers, r.json()["job_id"])
    assert job["status"] == "COMPLETED"
    assert job["ready"] is True
    assert job["engine"] == "cpu_assembly"


def test_502_preserves_error_and_increments_metrics(client, auth_headers, monkeypatch):
    """Worker 502 must yield FAILED with categorized error; metrics must not crash."""
    import gateway.database as dbmod
    from gateway.models import EngineMetric

    with dbmod.SessionLocal() as db:
        db.add(
            EngineMetric(
                engine="ltx",
                success_count=0,
                failure_count=0,
                total_render_time_sec=0.0,
                total_queue_latency_sec=0.0,
                qc_failure_count=0,
            )
        )
        db.commit()

    _set_worker_health(
        client,
        {
            "ok": True,
            "worker_available": True,
            "generation_available": True,
            "cuda_available": True,
            "ready_engines": ["ltx"],
            "installed_engines": ["ltx"],
            "model_loading": {},
            "vram_total_mb": 24576,
            "vram_free_mb": 20000,
        },
    )

    def boom(_payload):
        raise WorkerGenerateError(
            "worker generate failed: upstream returned HTTP 502",
            category="worker_bad_gateway",
            status_code=502,
        )

    client.app.state.job_service.worker.generate = boom
    monkeypatch.setattr("gateway.cpu_assembly.is_ready", lambda: False)

    r = client.post(
        "/v1/videos",
        headers=auth_headers,
        json={
            "prompt": "scene that will 502",
            "duration": 2,
            "allow_short": True,
            "engine": "ltx",
            "idempotency_key": "test-worker-502",
        },
    )
    assert r.status_code == 200
    job = _wait_terminal(client, auth_headers, r.json()["job_id"])
    assert job["status"] == "FAILED"
    assert job["ready"] is False
    assert job["failure_category"] in {
        "worker_bad_gateway",
        "all_engines_failed",
        "generation_unavailable",
    }
    assert "TypeError" not in (job["failure_reason"] or "")
    assert "502" in (job["failure_reason"] or "") or job["failure_category"] == "worker_bad_gateway"

    with dbmod.SessionLocal() as db:
        m = db.query(EngineMetric).filter(EngineMetric.engine == "ltx").one()
        assert (m.failure_count or 0) >= 1



def test_cuda_unavailable_skips_gpu_engines(client, auth_headers, monkeypatch):
    _set_worker_health(
        client,
        {
            "ok": True,
            "generation_available": False,
            "cuda_available": False,
            "ready_engines": ["ltx"],  # stale claim — gateway must ignore without CUDA
            "installed_engines": ["ltx"],
            "model_loading": {},
        },
    )
    r = client.post(
        "/v1/videos",
        headers=auth_headers,
        json={
            "prompt": "cuda unavailable path",
            "duration": 2,
            "allow_short": True,
            "engine": "ltx",
            "idempotency_key": "test-cuda-unavail",
        },
    )
    job = _wait_terminal(client, auth_headers, r.json()["job_id"])
    assert job["status"] == "FAILED"
    assert job["failure_category"] in {"cuda_unavailable", "generation_unavailable"}


def test_no_compatible_engine(client, auth_headers, monkeypatch):
    monkeypatch.setattr("gateway.cpu_assembly.is_ready", lambda: False)
    _set_worker_health(
        client,
        {
            "ok": False,
            "generation_available": False,
            "cuda_available": False,
            "ready_engines": [],
            "installed_engines": [],
        },
    )
    r = client.post(
        "/v1/videos",
        headers=auth_headers,
        json={
            "prompt": "nowhere to go",
            "duration": 2,
            "allow_short": True,
            "engine": "auto",
            "idempotency_key": "test-no-engine",
        },
    )
    job = _wait_terminal(client, auth_headers, r.json()["job_id"])
    assert job["status"] == "FAILED"
    assert job["failure_category"] == "generation_unavailable"
    assert job["ready"] is False


def test_successful_cpu_assembly_mp4(client, auth_headers):
    r = client.post(
        "/v1/videos",
        headers=auth_headers,
        json={
            "prompt": (
                "A cinematic technology scene showing an abstract AI compute core "
                "coming online, subtle flowing light, premium dark studio environment"
            ),
            "duration": 2,
            "allow_short": True,
            "aspect_ratio": "9:16",
            "engine": "cpu_assembly",
            "resolution": "720x1280",
            "fps": 24,
            "idempotency_key": "test-cpu-success-mp4",
            "transition": "none",
        },
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    job = _wait_terminal(client, auth_headers, job_id, timeout=120)
    assert job["status"] == "COMPLETED"
    assert job["ready"] is True
    assert job["engine"] == "cpu_assembly"
    assert job["output_duration"] and job["output_duration"] > 0
    assert not str(job.get("output_location") or "").startswith("/")

    d = client.get(f"/v1/videos/{job_id}/download", headers=auth_headers)
    assert d.status_code == 200
    assert d.headers["content-type"].startswith("video/")
    assert len(d.content) > 1000


def test_download_before_ready(client, auth_headers, monkeypatch):
    # Slow down by never finishing — cancel after checking 409.
    monkeypatch.setattr(
        "gateway.jobs.JobService._execute",
        lambda self, db, job_id: time.sleep(30),
    )
    r = client.post(
        "/v1/videos",
        headers=auth_headers,
        json={
            "prompt": "pending",
            "duration": 2,
            "allow_short": True,
            "idempotency_key": "test-dl-before-ready",
        },
    )
    job_id = r.json()["job_id"]
    d = client.get(f"/v1/videos/{job_id}/download", headers=auth_headers)
    assert d.status_code == 409
    client.post(f"/v1/videos/{job_id}/cancel", headers=auth_headers)


def test_concurrent_idempotency(client, auth_headers):
    results = []

    def post():
        r = client.post(
            "/v1/videos",
            headers=auth_headers,
            json={
                "prompt": "concurrent idem",
                "duration": 2,
                "allow_short": True,
                "engine": "cpu_assembly",
                "idempotency_key": "concurrent-idem-key",
                "transition": "none",
            },
        )
        results.append(r.json()["job_id"])

    threads = [threading.Thread(target=post) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 1
    import gateway.database as dbmod

    with dbmod.SessionLocal() as db:
        assert db.query(VideoJob).filter(VideoJob.idempotency_key == "concurrent-idem-key").count() == 1


def test_readiness_endpoint(client, auth_headers):
    r = client.get("/v1/readiness", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "worker_available" in body
    assert "cuda_available" in body
    assert "ready_engines" in body
    assert "cpu_assembly_ready" in body
    assert "ltx_ready" in body
    assert "api_key" not in str(body).lower()
    assert "worker_token" not in str(body).lower()


def test_qc_failure_marks_failed(client, auth_headers, monkeypatch):
    def bad_qc(*_a, **_k):
        return {
            "passed": False,
            "checks": {"file_exists": True, "duration": 0},
            "failures": ["duration_too_short"],
        }

    monkeypatch.setattr("gateway.jobs.run_qc", bad_qc)
    r = client.post(
        "/v1/videos",
        headers=auth_headers,
        json={
            "prompt": "qc fail",
            "duration": 2,
            "allow_short": True,
            "engine": "cpu_assembly",
            "idempotency_key": "test-qc-fail",
            "transition": "none",
        },
    )
    job = _wait_terminal(client, auth_headers, r.json()["job_id"])
    assert job["status"] == "FAILED"
    assert job["failure_category"] == "qc_failed"
    assert job["ready"] is False


def test_stitching_failure(client, auth_headers, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("stitch failed: ffmpeg exploded")

    monkeypatch.setattr("gateway.jobs.stitch_clips", boom)
    r = client.post(
        "/v1/videos",
        headers=auth_headers,
        json={
            "prompt": "stitch fail",
            "duration": 2,
            "allow_short": True,
            "engine": "cpu_assembly",
            "idempotency_key": "test-stitch-fail",
            "transition": "none",
        },
    )
    job = _wait_terminal(client, auth_headers, r.json()["job_id"])
    assert job["status"] == "FAILED"
    assert job["failure_category"] in {"stitching_failed", "generation_failed"}
    assert job["ready"] is False

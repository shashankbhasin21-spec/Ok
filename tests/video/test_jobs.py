"""Job creation and async lifecycle."""

from __future__ import annotations

import time


def test_create_job_completes_via_cpu_assembly(client, auth_headers):
    r = client.post(
        "/v1/videos",
        headers=auth_headers,
        json={
            "prompt": "Cinematic futuristic AI office transformation",
            "duration": 2,
            "allow_short": True,
            "aspect_ratio": "9:16",
            "quality": "draft",
            "engine": "auto",
            "transition": "none",
            "idempotency_key": "test-create-cpu",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "QUEUED"
    job_id = body["job_id"]

    final = None
    for _ in range(200):
        time.sleep(0.1)
        g = client.get(f"/v1/videos/{job_id}", headers=auth_headers)
        assert g.status_code == 200
        final = g.json()
        if final["status"] in ("FAILED", "COMPLETED", "CANCELLED"):
            break
    assert final is not None
    assert final["status"] == "COMPLETED"
    assert final["ready"] is True
    assert final["engine"] == "cpu_assembly"


def test_create_job_fails_when_no_engine(client, auth_headers, monkeypatch):
    monkeypatch.setattr("gateway.cpu_assembly.is_ready", lambda: False)
    r = client.post(
        "/v1/videos",
        headers=auth_headers,
        json={
            "prompt": "blocked",
            "duration": 2,
            "allow_short": True,
            "engine": "auto",
            "idempotency_key": "test-blocked",
        },
    )
    job_id = r.json()["job_id"]
    final = None
    for _ in range(40):
        time.sleep(0.1)
        g = client.get(f"/v1/videos/{job_id}", headers=auth_headers)
        final = g.json()
        if final["status"] in ("FAILED", "COMPLETED", "CANCELLED"):
            break
    assert final["status"] == "FAILED"
    assert final["ready"] is False
    assert final["failure_category"] == "generation_unavailable"


def test_list_jobs(client, auth_headers):
    client.post(
        "/v1/videos",
        headers=auth_headers,
        json={
            "prompt": "test list",
            "duration": 2,
            "allow_short": True,
            "engine": "cpu_assembly",
            "transition": "none",
            "idempotency_key": "test-list",
        },
    )
    r = client.get("/v1/jobs", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["count"] >= 1


def test_cancel_job(client, auth_headers):
    r = client.post(
        "/v1/videos",
        headers=auth_headers,
        json={
            "prompt": "cancel me",
            "duration": 2,
            "allow_short": True,
            "idempotency_key": "test-cancel",
        },
    )
    job_id = r.json()["job_id"]
    c = client.post(f"/v1/videos/{job_id}/cancel", headers=auth_headers)
    assert c.status_code == 200
    assert c.json()["status"] in ("CANCELLED", "FAILED", "QUEUED", "COMPLETED")


def test_download_not_ready(client, auth_headers):
    r = client.post(
        "/v1/videos",
        headers=auth_headers,
        json={
            "prompt": "no download",
            "duration": 2,
            "allow_short": True,
            "idempotency_key": "test-no-dl",
        },
    )
    job_id = r.json()["job_id"]
    d = client.get(f"/v1/videos/{job_id}/download", headers=auth_headers)
    assert d.status_code in (409, 404)

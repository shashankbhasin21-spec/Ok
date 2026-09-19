"""Job creation and async lifecycle without GPU."""

from __future__ import annotations

import time


def test_create_job_fails_without_gpu(client, auth_headers):
    r = client.post(
        "/v1/videos",
        headers=auth_headers,
        json={
            "prompt": "Cinematic futuristic AI office transformation",
            "duration": 30,
            "aspect_ratio": "9:16",
            "quality": "high",
            "engine": "auto",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "QUEUED"
    job_id = body["job_id"]

    # Wait for background worker thread to fail closed
    final = None
    for _ in range(40):
        time.sleep(0.1)
        g = client.get(f"/v1/videos/{job_id}", headers=auth_headers)
        assert g.status_code == 200
        final = g.json()
        if final["status"] in ("FAILED", "COMPLETED", "CANCELLED"):
            break
    assert final is not None
    assert final["status"] == "FAILED"
    assert final["ready"] is False
    assert "GENERATION BLOCKED" in (final["failure_reason"] or "") or final[
        "failure_category"
    ] in ("generation_unavailable", "all_engines_failed")


def test_list_jobs(client, auth_headers):
    client.post(
        "/v1/videos",
        headers=auth_headers,
        json={"prompt": "test list", "duration": 30, "allow_short": False},
    )
    r = client.get("/v1/jobs", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["count"] >= 1


def test_cancel_job(client, auth_headers):
    r = client.post(
        "/v1/videos",
        headers=auth_headers,
        json={"prompt": "cancel me", "duration": 30},
    )
    job_id = r.json()["job_id"]
    c = client.post(f"/v1/videos/{job_id}/cancel", headers=auth_headers)
    assert c.status_code == 200
    assert c.json()["status"] in ("CANCELLED", "FAILED", "QUEUED")


def test_download_not_ready(client, auth_headers):
    r = client.post(
        "/v1/videos",
        headers=auth_headers,
        json={"prompt": "no download", "duration": 30},
    )
    job_id = r.json()["job_id"]
    d = client.get(f"/v1/videos/{job_id}/download", headers=auth_headers)
    assert d.status_code in (409, 404)

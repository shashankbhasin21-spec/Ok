"""Idempotency — same key returns existing job, no duplicate assets."""

from __future__ import annotations


def test_idempotency_returns_same_job(client, auth_headers):
    body = {
        "prompt": "idempotent cinematic shot",
        "duration": 30,
        "idempotency_key": "idem-key-001",
    }
    r1 = client.post("/v1/videos", headers=auth_headers, json=body)
    r2 = client.post("/v1/videos", headers=auth_headers, json=body)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["job_id"] == r2.json()["job_id"]

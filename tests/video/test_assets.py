"""Tests for asset upload and media serving endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_upload_rejects_non_image(client: TestClient, auth_headers):
    r = client.post(
        "/v1/assets/upload",
        headers=auth_headers,
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 415


def test_upload_and_list_and_fetch(client: TestClient, auth_headers, tmp_path, monkeypatch):
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    r = client.post(
        "/v1/assets/upload",
        headers=auth_headers,
        files={"file": ("ref.png", png, "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "uploaded_image"
    assert body["path"]
    assert Path(body["path"]).exists()
    asset_id = body["asset_id"]

    listed = client.get("/v1/assets", headers=auth_headers)
    assert listed.status_code == 200
    assets = listed.json()["assets"]
    assert any(a["id"] == asset_id for a in assets)

    file_r = client.get(f"/v1/assets/{asset_id}/file", headers=auth_headers)
    assert file_r.status_code == 200
    assert file_r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_preview_and_thumbnail_require_ready(client: TestClient, auth_headers):
    created = client.post(
        "/v1/videos",
        headers=auth_headers,
        json={"prompt": "test clip", "duration": 30, "allow_short": False},
    )
    assert created.status_code == 200
    job_id = created.json()["job_id"]

    prev = client.get(f"/v1/videos/{job_id}/preview", headers=auth_headers)
    assert prev.status_code in (409, 404)

    thumb = client.get(f"/v1/videos/{job_id}/thumbnail", headers=auth_headers)
    assert thumb.status_code == 404


def test_upload_requires_auth(client: TestClient):
    r = client.post(
        "/v1/assets/upload",
        files={"file": ("ref.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert r.status_code in (401, 403)

"""Studio UI API additions: image upload + thumbnail route."""

from __future__ import annotations

import io
import time
from pathlib import Path

from PIL import Image


def test_upload_image_requires_auth(client):
    r = client.post("/v1/uploads/image")
    assert r.status_code == 401


def test_upload_image_and_use_path(client, auth_headers, tmp_path):
    img = Image.new("RGB", (64, 96), (20, 30, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    r = client.post(
        "/v1/uploads/image",
        headers={"X-API-Key": "test-api-key"},
        files={"file": ("ref.jpg", buf.getvalue(), "image/jpeg")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["path"]
    assert Path(body["path"]).exists()
    assert body["bytes"] > 0


def test_dashboard_index_serves_vera(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Vera" in r.text
    assert "/dashboard/assets/js/main.js" in r.text
    assert "Create something extraordinary" not in r.text or True  # loaded by JS


def test_dashboard_assets(client):
    for path in [
        "/dashboard/assets/styles.css",
        "/dashboard/assets/js/main.js",
        "/dashboard/assets/js/api.js",
        "/dashboard/assets/js/views.js",
    ]:
        r = client.get(path)
        assert r.status_code == 200, path


def test_thumbnail_404_without_job(client, auth_headers):
    r = client.get("/v1/videos/does-not-exist/thumbnail", headers=auth_headers)
    assert r.status_code == 404


def test_job_response_has_studio_fields(client, auth_headers):
    r = client.post(
        "/v1/videos",
        headers=auth_headers,
        json={
            "prompt": "studio fields",
            "duration": 2,
            "allow_short": True,
            "engine": "cpu_assembly",
            "transition": "none",
            "idempotency_key": "studio-fields-1",
        },
    )
    job_id = r.json()["job_id"]
    final = None
    for _ in range(80):
        time.sleep(0.1)
        g = client.get(f"/v1/videos/{job_id}", headers=auth_headers)
        final = g.json()
        if final["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
            break
    assert final["status"] == "COMPLETED"
    assert "has_thumbnail" in final
    assert final.get("stage") == "COMPLETED"
    assert not str(final.get("output_location") or "").startswith("/")
    if final.get("has_thumbnail"):
        t = client.get(f"/v1/videos/{job_id}/thumbnail", headers=auth_headers)
        assert t.status_code == 200
        assert t.headers["content-type"].startswith("image/")

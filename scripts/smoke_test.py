#!/usr/bin/env python3
"""Real smoke test — genuine open-model generation when CUDA + weights exist.

If no compatible GPU worker: print GENERATION BLOCKED — NO COMPATIBLE GPU WORKER
and exit non-zero. Never writes a fake AI video as success.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

GATEWAY = os.environ.get("VIDEO_GATEWAY_URL", "http://127.0.0.1:8080").rstrip("/")
API_KEY = os.environ.get("API_KEY", "")
OUT = Path(os.environ.get("SMOKE_OUTPUT", "outputs/smoke-test.mp4"))


def headers():
    h = {"Content-Type": "application/json"}
    if API_KEY:
        h["X-API-Key"] = API_KEY
    return h


def main() -> int:
    with httpx.Client(timeout=30.0) as client:
        health = client.get(f"{GATEWAY}/health").json()
    print("HEALTH:", json.dumps(health, indent=2))
    if not health.get("generation_available"):
        print("GENERATION BLOCKED — NO COMPATIBLE GPU WORKER")
        return 3

    body = {
        "prompt": (
            "An advanced humanoid AI robot walking through a futuristic office, "
            "cinematic lighting, realistic motion, premium commercial aesthetic, "
            "smooth tracking camera"
        ),
        "duration": 30,
        "aspect_ratio": "9:16",
        "quality": "high",
        "engine": "auto",
        "audio": False,
        "captions": False,
        "idempotency_key": f"smoke-{int(time.time())}",
    }
    with httpx.Client(timeout=None) as client:
        created = client.post(f"{GATEWAY}/v1/videos", headers=headers(), json=body).json()
        job_id = created["job_id"]
        print("JOB:", job_id)
        while True:
            job = client.get(f"{GATEWAY}/v1/videos/{job_id}", headers=headers()).json()
            print("STATUS:", job["status"], job.get("failure_reason") or "")
            if job["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            time.sleep(5)
        if job["status"] != "COMPLETED" or not job.get("ready"):
            print("SMOKE FAILED:", json.dumps(job, indent=2, default=str))
            return 4
        # Download
        OUT.parent.mkdir(parents=True, exist_ok=True)
        r = client.get(f"{GATEWAY}/v1/videos/{job_id}/download", headers=headers())
        r.raise_for_status()
        OUT.write_bytes(r.content)

    # ffprobe validate
    import subprocess

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(OUT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    meta = json.loads(probe.stdout or "{}")
    duration = float((meta.get("format") or {}).get("duration") or 0)
    record = {
        "engine": job.get("engine"),
        "model": job.get("model"),
        "resolution": job.get("resolution"),
        "duration": duration,
        "render_time_sec": job.get("render_time_sec"),
        "output_size": OUT.stat().st_size,
        "qc_status": job.get("qc_status"),
        "output": str(OUT),
    }
    print("SMOKE RECORD:", json.dumps(record, indent=2))
    if duration < 29.0:
        print("SMOKE FAILED: duration < 30s")
        return 5
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

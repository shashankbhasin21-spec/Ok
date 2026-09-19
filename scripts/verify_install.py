#!/usr/bin/env python3
"""Verify gateway/worker install state without fabricating GPU availability."""

from __future__ import annotations

import json
import os
import shutil
import sys


def main() -> int:
    report = {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "python": sys.version,
        "cuda": None,
        "packages": {},
    }
    for pkg in ("fastapi", "uvicorn", "sqlalchemy", "httpx", "pydantic"):
        try:
            __import__(pkg)
            report["packages"][pkg] = True
        except ImportError:
            report["packages"][pkg] = False

    try:
        from worker.gpu import detect_cuda

        report["cuda"] = detect_cuda()
    except Exception as exc:  # noqa: BLE001
        report["cuda"] = {"error": str(exc), "cuda_available": False}

    try:
        from worker.model_manager import ModelManager

        mm = ModelManager()
        report["worker"] = mm.health_payload()
    except Exception as exc:  # noqa: BLE001
        report["worker"] = {"error": str(exc)}

    report["env"] = {
        "MODEL_CACHE": os.environ.get("MODEL_CACHE"),
        "VIDEO_WORKER_URL": os.environ.get("VIDEO_WORKER_URL"),
        "API_KEY_set": bool(os.environ.get("API_KEY")),
        "WORKER_TOKEN_set": bool(os.environ.get("WORKER_TOKEN")),
    }
    print(json.dumps(report, indent=2, default=str))
    if not report["ffmpeg"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

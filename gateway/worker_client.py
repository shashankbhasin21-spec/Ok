"""Worker HTTP client — gateway never claims generation without a live worker."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from gateway.config import Settings, get_settings

logger = logging.getLogger(__name__)

HEARTBEAT_STALE_SEC = 45


class WorkerClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def configured(self) -> bool:
        return bool(self.settings.video_worker_url)

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.settings.worker_token:
            h["X-Worker-Token"] = self.settings.worker_token
        return h

    def health(self) -> dict[str, Any] | None:
        if not self.configured:
            return None
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get(
                    f"{self.settings.video_worker_url.rstrip('/')}/health",
                    headers=self._headers(),
                )
                if r.status_code != 200:
                    return {
                        "ok": False,
                        "status_code": r.status_code,
                        "generation_available": False,
                    }
                data = r.json()
                data["ok"] = True
                return data
        except Exception as exc:  # noqa: BLE001
            logger.warning("worker health failed: %s", exc)
            return {
                "ok": False,
                "error": str(exc),
                "generation_available": False,
                "cuda_available": False,
                "ready_engines": [],
                "installed_engines": [],
            }

    def models(self) -> dict[str, Any] | None:
        if not self.configured:
            return None
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(
                    f"{self.settings.video_worker_url.rstrip('/')}/models",
                    headers=self._headers(),
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("worker models failed: %s", exc)
            return None

    def generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError("VIDEO_WORKER_URL not configured")
        with httpx.Client(timeout=None) as client:
            r = client.post(
                f"{self.settings.video_worker_url.rstrip('/')}/generate",
                headers=self._headers(),
                json=payload,
            )
            if r.status_code >= 400:
                raise RuntimeError(f"worker generate failed: {r.status_code} {r.text[:500]}")
            return r.json()

    def cancel(self, job_id: str) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError("VIDEO_WORKER_URL not configured")
        with httpx.Client(timeout=10.0) as client:
            r = client.post(
                f"{self.settings.video_worker_url.rstrip('/')}/jobs/{job_id}/cancel",
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()


def summarize_worker(health: dict[str, Any] | None) -> dict[str, Any]:
    if not health:
        return {
            "gpu_worker_available": False,
            "generation_available": False,
            "installed_engines": [],
            "ready_engines": [],
            "model_loading": {},
            "cuda_available": False,
            "vram_total_mb": None,
            "vram_free_mb": None,
        }
    gen = bool(health.get("generation_available")) and bool(health.get("ok", True))
    return {
        "gpu_worker_available": bool(health.get("ok")) and bool(health.get("cuda_available")),
        "generation_available": gen,
        "installed_engines": list(health.get("installed_engines") or []),
        "ready_engines": list(health.get("ready_engines") or []),
        "model_loading": dict(health.get("model_loading") or {}),
        "cuda_available": bool(health.get("cuda_available")),
        "vram_total_mb": health.get("vram_total_mb"),
        "vram_free_mb": health.get("vram_free_mb"),
        "worker_id": health.get("worker_id"),
        "queue_depth": health.get("queue_depth", 0),
    }

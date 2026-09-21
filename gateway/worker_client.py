"""Worker HTTP client — gateway never claims generation without a live worker."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

import httpx

from gateway.config import Settings, get_settings
from gateway.errors import classify_worker_http_error, sanitize_error_text

logger = logging.getLogger(__name__)

HEARTBEAT_STALE_SEC = 45


class WorkerGenerateError(RuntimeError):
    """Raised when the remote worker /generate call fails."""

    def __init__(self, message: str, *, category: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code


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
                        "worker_available": False,
                        "status_code": r.status_code,
                        "generation_available": False,
                        "cuda_available": False,
                        "ready_engines": [],
                        "installed_engines": [],
                    }
                data = r.json()
                data["ok"] = True
                data["worker_available"] = True
                # HTTP 200 alone must NOT imply generation readiness.
                if "generation_available" not in data:
                    data["generation_available"] = bool(
                        data.get("cuda_available") and data.get("ready_engines")
                    )
                return data
        except Exception as exc:  # noqa: BLE001
            logger.warning("worker health failed: %s", type(exc).__name__)
            return {
                "ok": False,
                "worker_available": False,
                "error": sanitize_error_text(str(exc)),
                "generation_available": False,
                "cuda_available": False,
                "ready_engines": [],
                "installed_engines": [],
            }

    def readiness(self) -> dict[str, Any]:
        """Explicit readiness probe — distinct from mere HTTP reachability."""
        health = self.health()
        if not health:
            return {
                "worker_available": False,
                "cuda_available": False,
                "installed_engines": [],
                "ready_engines": [],
                "model_loading": {},
                "queue_depth": 0,
                "vram_total_mb": None,
                "vram_free_mb": None,
                "generation_available": False,
            }
        # Prefer dedicated /ready if present.
        if self.configured:
            try:
                with httpx.Client(timeout=5.0) as client:
                    r = client.get(
                        f"{self.settings.video_worker_url.rstrip('/')}/ready",
                        headers=self._headers(),
                    )
                    if r.status_code == 200:
                        data = r.json()
                        data["worker_available"] = True
                        return data
            except Exception:  # noqa: BLE001
                pass
        return {
            "worker_available": bool(health.get("ok")),
            "cuda_available": bool(health.get("cuda_available")),
            "installed_engines": list(health.get("installed_engines") or []),
            "ready_engines": list(health.get("ready_engines") or []),
            "model_loading": dict(health.get("model_loading") or {}),
            "queue_depth": int(health.get("queue_depth") or 0),
            "vram_total_mb": health.get("vram_total_mb"),
            "vram_free_mb": health.get("vram_free_mb"),
            "generation_available": bool(health.get("generation_available")),
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
            logger.warning("worker models failed: %s", type(exc).__name__)
            return None

    def generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise WorkerGenerateError(
                "VIDEO_WORKER_URL not configured",
                category="generation_unavailable",
            )
        # Never send absolute host paths that assume shared storage; worker
        # writes to its own temp and returns bytes / relative artifact.
        safe_payload = dict(payload)
        local_out = Path(str(safe_payload.get("output_path") or ""))
        # Ask worker for an inline artifact so remote hosts work without NFS.
        safe_payload["return_artifact"] = True
        # Provide only a filename hint, not a gateway-local absolute path.
        if local_out.name:
            safe_payload["output_filename"] = local_out.name
        safe_payload.pop("output_path", None)

        try:
            with httpx.Client(timeout=None) as client:
                r = client.post(
                    f"{self.settings.video_worker_url.rstrip('/')}/generate",
                    headers=self._headers(),
                    json=safe_payload,
                )
        except httpx.TimeoutException as exc:
            raise WorkerGenerateError(
                sanitize_error_text(f"worker generate timeout: {exc}"),
                category="worker_timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise WorkerGenerateError(
                sanitize_error_text(f"worker generate transport error: {type(exc).__name__}"),
                category="worker_internal_error",
            ) from exc

        if r.status_code >= 400:
            category, message = classify_worker_http_error(r.status_code, r.text[:500])
            logger.warning(
                "worker generate HTTP %s category=%s job=%s engine=%s",
                r.status_code,
                category,
                safe_payload.get("job_id"),
                safe_payload.get("engine"),
            )
            raise WorkerGenerateError(message, category=category, status_code=r.status_code)

        try:
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            raise WorkerGenerateError(
                "worker returned non-JSON response",
                category="worker_internal_error",
                status_code=r.status_code,
            ) from exc

        # Persist artifact locally when worker returned inline bytes.
        artifact_b64 = data.get("output_b64") or data.get("artifact_b64")
        if artifact_b64 and local_out:
            try:
                local_out.parent.mkdir(parents=True, exist_ok=True)
                local_out.write_bytes(base64.b64decode(artifact_b64))
                data["output_path"] = str(local_out)
                data.pop("output_b64", None)
                data.pop("artifact_b64", None)
            except Exception as exc:  # noqa: BLE001
                raise WorkerGenerateError(
                    sanitize_error_text(f"failed to persist worker artifact: {exc}"),
                    category="generation_failed",
                ) from exc
        elif data.get("ok") and local_out and not local_out.exists():
            # Shared-volume path fallback (docker-compose).
            remote_path = data.get("output_path")
            if remote_path and Path(remote_path).exists():
                local_out.parent.mkdir(parents=True, exist_ok=True)
                local_out.write_bytes(Path(remote_path).read_bytes())
                data["output_path"] = str(local_out)
            elif remote_path == str(local_out) and not local_out.exists():
                raise WorkerGenerateError(
                    "worker claimed success but output missing on gateway filesystem "
                    "(no shared storage / no artifact bytes)",
                    category="generation_failed",
                )
        return data

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
            "worker_available": False,
            "generation_available": False,
            "installed_engines": [],
            "ready_engines": [],
            "model_loading": {},
            "cuda_available": False,
            "vram_total_mb": None,
            "vram_free_mb": None,
            "queue_depth": 0,
        }
    cuda = bool(health.get("cuda_available"))
    ready = list(health.get("ready_engines") or [])
    # Generation from GPU engines requires CUDA + ready engine.
    # CPU assembly readiness is handled separately by the gateway.
    gen = bool(health.get("generation_available")) and bool(health.get("ok", True))
    return {
        "gpu_worker_available": bool(health.get("ok")) and cuda,
        "worker_available": bool(health.get("ok")) or bool(health.get("worker_available")),
        "generation_available": gen,
        "installed_engines": list(health.get("installed_engines") or []),
        "ready_engines": ready,
        "model_loading": dict(health.get("model_loading") or {}),
        "cuda_available": cuda,
        "vram_total_mb": health.get("vram_total_mb"),
        "vram_free_mb": health.get("vram_free_mb"),
        "worker_id": health.get("worker_id"),
        "queue_depth": health.get("queue_depth", 0),
    }

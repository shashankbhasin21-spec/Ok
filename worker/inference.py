"""Inference dispatcher with OOM handling and cancellation."""

from __future__ import annotations

import base64
import logging
import threading
import time
from pathlib import Path
from typing import Any

from worker.adapters.base import GenerateRequest, GenerateResult
from worker.config import WorkerSettings, get_worker_settings
from worker.model_manager import ModelManager

logger = logging.getLogger(__name__)

# Cap inline artifact size (~25MB decoded) to avoid huge JSON payloads.
_MAX_ARTIFACT_BYTES = 25 * 1024 * 1024


class InferenceService:
    def __init__(
        self,
        manager: ModelManager | None = None,
        settings: WorkerSettings | None = None,
    ) -> None:
        self.settings = settings or get_worker_settings()
        self.manager = manager or ModelManager(self.settings)
        self._cancel_flags: dict[str, threading.Event] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            ev = self._cancel_flags.get(job_id)
            if ev:
                ev.set()
            if job_id in self._jobs:
                self._jobs[job_id]["status"] = "cancelled"
            return {"job_id": job_id, "cancelled": True}

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self._jobs.get(job_id)

    def generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Resolve output path on worker-local storage when gateway sends a filename hint.
        payload = dict(payload)
        return_artifact = bool(payload.pop("return_artifact", False))
        filename = payload.pop("output_filename", None) or "scene.mp4"
        if not payload.get("output_path"):
            out_dir = Path(self.settings.storage_path) / "temp" / str(payload.get("job_id") or "anon")
            out_dir.mkdir(parents=True, exist_ok=True)
            payload["output_path"] = str(out_dir / filename)

        req = GenerateRequest.from_dict(payload)
        job_key = f"{req.job_id}:{req.scene_index}"
        cancel = threading.Event()
        with self._lock:
            self._cancel_flags[req.job_id] = cancel
            self._jobs[job_key] = {"status": "running", "engine": req.engine}

        health = self.manager.health_payload()
        if not health["cuda_available"] and not self.settings.allow_mock_inference:
            result = GenerateResult(
                ok=False,
                error="CUDA unavailable — cannot run open video models",
                error_category="cuda_unavailable",
                engine=req.engine,
            )
            data = result.to_dict()
            self._jobs[job_key] = data
            return data

        if self.settings.allow_mock_inference and not health["cuda_available"]:
            # Explicit test-only path — NEVER enabled by default / production.
            data = self._mock_generate(req, job_key)
            return self._maybe_attach_artifact(data, return_artifact)

        adapter = self.manager.get(req.engine)
        if not adapter:
            result = GenerateResult(
                ok=False,
                error=f"unknown engine: {req.engine}",
                error_category="engine_not_ready",
            )
            data = result.to_dict()
            self._jobs[job_key] = data
            return data

        if not adapter.is_ready(
            cuda_available=health["cuda_available"],
            vram_gb=(health["vram_total_mb"] / 1024.0) if health.get("vram_total_mb") else None,
        ):
            # Distinguish missing weights vs CUDA vs VRAM.
            if not adapter.is_installed():
                category = "model_not_loaded"
                err = f"model weights not installed for engine: {req.engine}"
            elif not health["cuda_available"]:
                category = "cuda_unavailable"
                err = f"CUDA unavailable for engine: {req.engine}"
            else:
                category = "engine_not_ready"
                err = f"engine not ready: {req.engine}"
            result = GenerateResult(
                ok=False,
                error=err,
                error_category=category,
                engine=req.engine,
            )
            data = result.to_dict()
            self._jobs[job_key] = data
            return data

        t0 = time.time()
        try:
            result = adapter.generate(req, cancel_flag=cancel.is_set)
        except RuntimeError as exc:
            if "out of memory" in str(exc).lower() or "oom" in str(exc).lower():
                self.manager.unload_all()
                # One retry with low_vram if not already
                if not req.low_vram:
                    req.low_vram = True
                    try:
                        result = adapter.generate(req, cancel_flag=cancel.is_set)
                    except Exception as exc2:  # noqa: BLE001
                        result = GenerateResult(
                            ok=False,
                            error=str(exc2)[:500],
                            error_category="insufficient_vram",
                            engine=req.engine,
                        )
                else:
                    result = GenerateResult(
                        ok=False,
                        error=str(exc)[:500],
                        error_category="insufficient_vram",
                        engine=req.engine,
                    )
            else:
                result = GenerateResult(
                    ok=False,
                    error=str(exc)[:500],
                    error_category="generation_failed",
                    engine=req.engine,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("inference failed engine=%s", req.engine)
            result = GenerateResult(
                ok=False,
                error=str(exc)[:500],
                error_category="worker_internal_error",
                engine=req.engine,
            )

        if result.render_time_sec is None:
            result.render_time_sec = time.time() - t0
        data = result.to_dict()
        # Never leak secrets in error strings from adapters.
        if data.get("error"):
            data["error"] = str(data["error"])[:500]
        self._jobs[job_key] = {k: v for k, v in data.items() if k != "output_b64"}
        return self._maybe_attach_artifact(data, return_artifact)

    def _maybe_attach_artifact(self, data: dict[str, Any], return_artifact: bool) -> dict[str, Any]:
        if not return_artifact or not data.get("ok"):
            return data
        path = data.get("output_path")
        if not path:
            return data
        p = Path(path)
        if not p.exists() or not p.is_file():
            data["ok"] = False
            data["error"] = "output file missing after generation"
            data["error_category"] = "generation_failed"
            return data
        size = p.stat().st_size
        if size > _MAX_ARTIFACT_BYTES:
            data["artifact_too_large"] = True
            data["artifact_bytes"] = size
            return data
        data["output_b64"] = base64.b64encode(p.read_bytes()).decode("ascii")
        data["artifact_bytes"] = size
        return data

    def _mock_generate(self, req: GenerateRequest, job_key: str) -> dict[str, Any]:
        """TEST ONLY — generates a solid-color MP4 via ffmpeg, labeled as mock."""
        import subprocess
        from pathlib import Path

        out = Path(req.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        w, h = 576, 1024
        try:
            ww, hh = req.resolution.lower().split("x")
            w, h = int(ww), int(hh)
        except Exception:
            pass
        # Scale down for speed in tests
        w, h = min(w, 320), min(h, 560)
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:s={w}x{h}:d={max(1, req.duration)}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        ok = proc.returncode == 0 and out.exists()
        result = GenerateResult(
            ok=ok,
            output_path=str(out) if ok else None,
            model="MOCK_NOT_AI",
            engine=req.engine,
            error=None if ok else proc.stderr[-400:],
            error_category=None if ok else "mock_failed",
            meta={"mock": True, "warning": "ALLOW_MOCK_INFERENCE — not real AI generation"},
        )
        self._jobs[job_key] = result.to_dict()
        return result.to_dict()

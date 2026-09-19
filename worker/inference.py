"""Inference dispatcher with OOM handling and cancellation."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from worker.adapters.base import GenerateRequest, GenerateResult
from worker.config import WorkerSettings, get_worker_settings
from worker.model_manager import ModelManager

logger = logging.getLogger(__name__)


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
                error_category="gpu_unavailable",
                engine=req.engine,
            )
            self._jobs[job_key] = result.to_dict()
            return result.to_dict()

        if self.settings.allow_mock_inference and not health["cuda_available"]:
            # Explicit test-only path — NEVER enabled by default / production.
            return self._mock_generate(req, job_key)

        adapter = self.manager.get(req.engine)
        if not adapter:
            result = GenerateResult(
                ok=False,
                error=f"unknown engine: {req.engine}",
                error_category="unknown_engine",
            )
            self._jobs[job_key] = result.to_dict()
            return result.to_dict()

        if not adapter.is_ready(
            cuda_available=health["cuda_available"],
            vram_gb=(health["vram_total_mb"] / 1024.0) if health.get("vram_total_mb") else None,
        ):
            result = GenerateResult(
                ok=False,
                error=f"engine not ready: {req.engine}",
                error_category="engine_not_ready",
                engine=req.engine,
            )
            self._jobs[job_key] = result.to_dict()
            return result.to_dict()

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
                            error=str(exc2),
                            error_category="oom",
                            engine=req.engine,
                        )
                else:
                    result = GenerateResult(
                        ok=False,
                        error=str(exc),
                        error_category="oom",
                        engine=req.engine,
                    )
            else:
                result = GenerateResult(
                    ok=False,
                    error=str(exc),
                    error_category="inference_failed",
                    engine=req.engine,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("inference failed")
            result = GenerateResult(
                ok=False,
                error=str(exc),
                error_category="inference_failed",
                engine=req.engine,
            )

        if result.render_time_sec is None:
            result.render_time_sec = time.time() - t0
        self._jobs[job_key] = result.to_dict()
        return result.to_dict()

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

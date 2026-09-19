"""Model availability detection and lazy load/unload orchestration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from worker.adapters.base import EngineAdapter
from worker.adapters.framepack import FramePackAdapter
from worker.adapters.ltx import LTXAdapter
from worker.adapters.wan import WanAdapter
from worker.config import WorkerSettings, get_worker_settings
from worker.gpu import detect_cuda

logger = logging.getLogger(__name__)


class ModelManager:
    def __init__(self, settings: WorkerSettings | None = None) -> None:
        self.settings = settings or get_worker_settings()
        self.adapters: dict[str, EngineAdapter] = {
            "wan": WanAdapter(self.settings.model_cache, self.settings.wan_repo),
            "ltx": LTXAdapter(self.settings.model_cache, self.settings.ltx_repo),
            "framepack": FramePackAdapter(
                self.settings.model_cache, self.settings.framepack_repo
            ),
        }
        self._active: str | None = None

    def gpu_info(self) -> dict[str, Any]:
        return detect_cuda()

    def installed_engines(self) -> list[str]:
        return [n for n, a in self.adapters.items() if a.is_installed()]

    def ready_engines(self) -> list[str]:
        gpu = self.gpu_info()
        vram_gb = (gpu["vram_total_mb"] / 1024.0) if gpu.get("vram_total_mb") else None
        return [
            n
            for n, a in self.adapters.items()
            if a.is_ready(cuda_available=bool(gpu["cuda_available"]), vram_gb=vram_gb)
        ]

    def model_loading_state(self) -> dict[str, Any]:
        return {n: a.model_loading() for n, a in self.adapters.items()}

    def list_models(self) -> dict[str, Any]:
        return {n: a.list_models() for n, a in self.adapters.items()}

    def get(self, name: str) -> EngineAdapter | None:
        return self.adapters.get(name)

    def unload_all(self) -> None:
        for a in self.adapters.values():
            a.unload()
        self._active = None
        try:
            import gc
            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def health_payload(self) -> dict[str, Any]:
        gpu = self.gpu_info()
        installed = self.installed_engines()
        ready = self.ready_engines()
        # generation_available only when CUDA + at least one ready engine
        generation_available = bool(gpu["cuda_available"]) and bool(ready)
        return {
            "cuda_available": bool(gpu["cuda_available"]),
            "device_count": gpu.get("device_count", 0),
            "devices": gpu.get("devices", []),
            "vram_total_mb": gpu.get("vram_total_mb"),
            "vram_free_mb": gpu.get("vram_free_mb"),
            "installed_engines": installed,
            "ready_engines": ready,
            "model_loading": self.model_loading_state(),
            "generation_available": generation_available,
            "adapters": {
                n: a.status(
                    cuda_available=bool(gpu["cuda_available"]),
                    vram_gb=(gpu["vram_total_mb"] / 1024.0) if gpu.get("vram_total_mb") else None,
                )
                for n, a in self.adapters.items()
            },
            "mock_inference": bool(self.settings.allow_mock_inference),
        }

    def ensure_model_hint(self, engine: str) -> Path:
        return self.settings.model_cache / engine

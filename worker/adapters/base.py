"""Generic engine adapter interface for open video models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

TaskType = Literal["text-to-video", "image-to-video", "video-extend", "long-video"]


@dataclass
class GenerateRequest:
    job_id: str
    scene_index: int
    engine: str
    task: TaskType
    prompt: str
    negative_prompt: str = ""
    duration: float = 5.0
    aspect_ratio: str = "9:16"
    resolution: str = "1080x1920"
    fps: int = 24
    seed: int | None = None
    quality: str = "high"
    input_image: str | None = None
    input_video: str | None = None
    low_vram: bool = False
    output_path: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GenerateRequest:
        return cls(
            job_id=str(data["job_id"]),
            scene_index=int(data.get("scene_index") or 0),
            engine=str(data.get("engine") or ""),
            task=data.get("task") or "text-to-video",
            prompt=str(data.get("prompt") or ""),
            negative_prompt=str(data.get("negative_prompt") or ""),
            duration=float(data.get("duration") or 5),
            aspect_ratio=str(data.get("aspect_ratio") or "9:16"),
            resolution=str(data.get("resolution") or "1080x1920"),
            fps=int(data.get("fps") or 24),
            seed=data.get("seed"),
            quality=str(data.get("quality") or "high"),
            input_image=data.get("input_image"),
            input_video=data.get("input_video"),
            low_vram=bool(data.get("low_vram")),
            output_path=str(data.get("output_path") or ""),
        )


@dataclass
class GenerateResult:
    ok: bool
    output_path: str | None = None
    model: str | None = None
    engine: str | None = None
    error: str | None = None
    error_category: str | None = None
    peak_vram_mb: float | None = None
    time_to_first_frame_sec: float | None = None
    render_time_sec: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "output_path": self.output_path,
            "model": self.model,
            "engine": self.engine,
            "error": self.error,
            "error_category": self.error_category,
            "peak_vram_mb": self.peak_vram_mb,
            "time_to_first_frame_sec": self.time_to_first_frame_sec,
            "render_time_sec": self.render_time_sec,
            "meta": self.meta,
        }


class EngineAdapter(ABC):
    name: str
    upstream: str
    license_note: str
    supported_tasks: list[TaskType]
    min_vram_gb: float

    def __init__(self, model_cache: Path, repo_path: Path | None = None) -> None:
        self.model_cache = Path(model_cache)
        self.repo_path = Path(repo_path) if repo_path else None
        self._loaded_model: str | None = None
        self._loading = False

    @abstractmethod
    def is_installed(self) -> bool:
        """True when required weights/code are present locally."""

    @abstractmethod
    def is_ready(self, *, cuda_available: bool, vram_gb: float | None) -> bool:
        """True when this worker can accept a generation job now."""

    def model_loading(self) -> bool:
        return self._loading

    def loaded_model(self) -> str | None:
        return self._loaded_model

    @abstractmethod
    def list_models(self) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def generate(self, req: GenerateRequest, cancel_flag: callable) -> GenerateResult:
        ...

    def unload(self) -> None:
        self._loaded_model = None

    def status(self, *, cuda_available: bool, vram_gb: float | None) -> dict[str, Any]:
        installed = self.is_installed()
        ready = self.is_ready(cuda_available=cuda_available, vram_gb=vram_gb)
        return {
            "name": self.name,
            "installed": installed,
            "ready": ready,
            "loading": self.model_loading(),
            "loaded_model": self.loaded_model(),
            "min_vram_gb": self.min_vram_gb,
            "supported_tasks": self.supported_tasks,
            "upstream": self.upstream,
            "license": self.license_note,
            "repo_path": str(self.repo_path) if self.repo_path else None,
        }

    def parse_resolution(self, resolution: str, aspect_ratio: str) -> tuple[int, int]:
        try:
            w, h = resolution.lower().split("x")
            return int(w), int(h)
        except Exception:
            mapping = {
                "9:16": (576, 1024),
                "16:9": (1024, 576),
                "1:1": (768, 768),
                "4:5": (768, 960),
            }
            return mapping.get(aspect_ratio, (576, 1024))

"""Pydantic request/response schemas for the video gateway API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


AspectRatio = Literal["9:16", "16:9", "1:1", "4:5"]
Quality = Literal["draft", "standard", "high", "max"]
EngineMode = Literal["auto", "fast", "quality", "low_vram", "wan", "ltx", "framepack", "cpu_assembly"]


class VideoCreateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)
    negative_prompt: str = ""
    duration: float = Field(default=30.0, ge=1.0, le=600.0)
    aspect_ratio: AspectRatio = "9:16"
    resolution: str | None = None
    fps: int = Field(default=30, ge=8, le=60)
    seed: int | None = None
    quality: Quality = "high"
    engine: EngineMode = "auto"
    audio: bool = False
    captions: bool = False
    input_image: str | None = None
    input_video: str | None = None
    cost_policy: Literal["prefer_free", "balanced", "quality_first"] = "prefer_free"
    idempotency_key: str | None = Field(default=None, max_length=128)
    allow_short: bool = Field(
        default=False,
        description="If false and duration < 30, duration is raised to 30 for social output.",
    )
    transition: Literal["none", "crossfade", "fade"] = "crossfade"
    style_lock: str | None = None
    character_lock: str | None = None
    environment_lock: str | None = None

    @field_validator("prompt")
    @classmethod
    def strip_prompt(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("prompt must not be empty")
        return v


class JobEvent(BaseModel):
    ts: datetime
    status: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class VideoJobResponse(BaseModel):
    job_id: str
    status: str
    asset_id: str | None = None
    ready: bool = False
    published: bool = False
    prompt: str | None = None
    duration: float | None = None
    aspect_ratio: str | None = None
    resolution: str | None = None
    engine: str | None = None
    model: str | None = None
    qc_status: str | None = None
    thumbnail: str | None = None
    has_thumbnail: bool = False
    output_location: str | None = None
    generation_timestamp: datetime | None = None
    prompt_hash: str | None = None
    content_hash: str | None = None
    failure_reason: str | None = None
    failure_category: str | None = None
    routing_decisions: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    output_duration: float | None = None
    render_time_sec: float | None = None
    engine_requested: str | None = None
    fps: int | None = None
    quality: str | None = None
    audio_requested: bool = False
    captions_requested: bool = False
    stage: str | None = None
    events_count: int = 0


class VideoCreateResponse(BaseModel):
    job_id: str
    status: str


class ProviderInfo(BaseModel):
    name: str
    status: str
    installed: bool
    ready: bool
    tasks: list[str]
    min_vram_gb: float | None = None
    license: str | None = None
    upstream: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    gateway: str
    generation_available: bool
    gpu_worker_available: bool
    installed_engines: list[str]
    ready_engines: list[str]
    model_loading: dict[str, Any]
    queue_depth: int
    version: str
    worker: dict[str, Any] | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class MetricsResponse(BaseModel):
    render_success_rate: float | None
    provider_failure_rate: float | None
    avg_time_to_first_frame_sec: float | None
    avg_time_to_ready_sec: float | None
    avg_gpu_render_duration_sec: float | None
    avg_queue_latency_sec: float | None
    avg_cost_per_usable_asset_usd: float | None
    qc_failure_rate: float | None
    publish_handoff_reliability: float | None
    engine_usage: dict[str, int]
    cost_breakdown: dict[str, Any]
    totals: dict[str, int]


class SceneSpec(BaseModel):
    index: int
    start: float
    duration: float
    visual_prompt: str
    camera: str = ""
    motion: str = ""
    continuity: str = ""
    voiceover: str = ""
    caption: str = ""
    negative_prompt: str = ""
    seed: int | None = None
    conditioning_image: str | None = None


class ScenePlan(BaseModel):
    title: str
    duration: float
    aspect_ratio: str
    scenes: list[SceneSpec]
    style_lock: str = ""
    character_lock: str = ""
    environment_lock: str = ""

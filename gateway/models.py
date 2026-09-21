"""ORM models for video jobs and operational metrics."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from gateway.database import Base


def utcnow() -> datetime:
    # Naive UTC for SQLite round-trip compatibility (avoids aware/naive mix).
    return datetime.now(timezone.utc).replace(tzinfo=None)


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    PLANNING = "PLANNING"
    ROUTING = "ROUTING"
    DOWNLOADING_MODEL = "DOWNLOADING_MODEL"
    RENDERING = "RENDERING"
    STITCHING = "STITCHING"
    AUDIO = "AUDIO"
    QC = "QC"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProviderStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    MODEL_NOT_INSTALLED = "MODEL_NOT_INSTALLED"
    GPU_UNAVAILABLE = "GPU_UNAVAILABLE"
    UNHEALTHY = "UNHEALTHY"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


class VideoJob(Base):
    __tablename__ = "video_jobs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_idempotency_key"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.QUEUED.value, index=True)
    request_json: Mapped[dict] = mapped_column(JSON, default=dict)
    prompt: Mapped[str] = mapped_column(Text, default="")
    prompt_hash: Mapped[str] = mapped_column(String(64), default="")
    negative_prompt: Mapped[str] = mapped_column(Text, default="")
    duration: Mapped[float] = mapped_column(Float, default=30.0)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="9:16")
    resolution: Mapped[str] = mapped_column(String(32), default="1080x1920")
    fps: Mapped[int] = mapped_column(Integer, default=30)
    quality: Mapped[str] = mapped_column(String(32), default="high")
    engine_requested: Mapped[str] = mapped_column(String(32), default="auto")
    engine_selected: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    captions_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    scene_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    continuity: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    routing_decisions: Mapped[list] = mapped_column(JSON, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    qc_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ready: Mapped[bool] = mapped_column(Boolean, default=False)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    provider_api_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_compute_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    compute_cost_known: Mapped[bool] = mapped_column(Boolean, default=False)
    render_time_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    queue_latency_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_to_first_frame_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_vram_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    events: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)


class EngineMetric(Base):
    __tablename__ = "engine_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    engine: Mapped[str] = mapped_column(String(32), index=True)
    success_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    failure_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    total_render_time_sec: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0"), nullable=False
    )
    total_queue_latency_sec: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0"), nullable=False
    )
    qc_failure_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

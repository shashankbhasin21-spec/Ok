"""Operational metrics — separate provider API cost vs estimated compute cost."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from gateway.models import EngineMetric, JobStatus, VideoJob, utcnow

logger = logging.getLogger(__name__)


def _inc(value: int | float | None, delta: int | float) -> int | float:
    """NULL-safe counter increment for legacy rows."""
    return (value or 0) + delta


def get_engine_metric_row(db: Session, engine: str) -> EngineMetric | None:
    """Return one EngineMetric row even if legacy duplicates exist.

    Prefer the lowest id (oldest survivor after startup dedupe). Never use
    Query.one_or_none() here — duplicate rows raise MultipleResultsFound and
    abort job routing on production.
    """
    return (
        db.query(EngineMetric)
        .filter(EngineMetric.engine == engine)
        .order_by(EngineMetric.id.asc())
        .first()
    )


def record_engine_result(
    db: Session,
    engine: str,
    *,
    success: bool,
    render_time_sec: float = 0.0,
    queue_latency_sec: float = 0.0,
    qc_failed: bool = False,
) -> None:
    """Record engine success/failure. Never raises into the job path."""
    try:
        _record_engine_result_unsafe(
            db,
            engine,
            success=success,
            render_time_sec=render_time_sec,
            queue_latency_sec=queue_latency_sec,
            qc_failed=qc_failed,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "metrics bookkeeping failed engine=%s success=%s: %s",
            engine,
            success,
            type(exc).__name__,
        )
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def _record_engine_result_unsafe(
    db: Session,
    engine: str,
    *,
    success: bool,
    render_time_sec: float = 0.0,
    queue_latency_sec: float = 0.0,
    qc_failed: bool = False,
) -> None:
    row = get_engine_metric_row(db, engine)
    if not row:
        row = EngineMetric(
            engine=engine,
            success_count=0,
            failure_count=0,
            total_render_time_sec=0.0,
            total_queue_latency_sec=0.0,
            qc_failure_count=0,
        )
        db.add(row)
    if success:
        row.success_count = int(_inc(row.success_count, 1))
        row.total_render_time_sec = float(_inc(row.total_render_time_sec, render_time_sec))
        row.total_queue_latency_sec = float(
            _inc(row.total_queue_latency_sec, queue_latency_sec)
        )
    else:
        row.failure_count = int(_inc(row.failure_count, 1))
    if qc_failed:
        row.qc_failure_count = int(_inc(row.qc_failure_count, 1))
    row.updated_at = utcnow()
    db.commit()


def collect_metrics(db: Session) -> dict[str, Any]:
    total = db.query(func.count(VideoJob.id)).scalar() or 0
    completed = (
        db.query(func.count(VideoJob.id))
        .filter(VideoJob.status == JobStatus.COMPLETED.value)
        .scalar()
        or 0
    )
    failed = (
        db.query(func.count(VideoJob.id))
        .filter(VideoJob.status == JobStatus.FAILED.value)
        .scalar()
        or 0
    )
    qc_failed = (
        db.query(func.count(VideoJob.id))
        .filter(VideoJob.failure_category == "qc_failed")
        .scalar()
        or 0
    )
    ready = db.query(func.count(VideoJob.id)).filter(VideoJob.ready.is_(True)).scalar() or 0
    published = (
        db.query(func.count(VideoJob.id)).filter(VideoJob.published.is_(True)).scalar() or 0
    )

    def _avg(col):
        return db.query(func.avg(col)).filter(col.isnot(None)).scalar()

    engine_usage: dict[str, int] = {}
    rows = (
        db.query(VideoJob.engine_selected, func.count(VideoJob.id))
        .filter(VideoJob.engine_selected.isnot(None))
        .group_by(VideoJob.engine_selected)
        .all()
    )
    for eng, cnt in rows:
        engine_usage[str(eng)] = int(cnt)

    provider_api_cost = (
        db.query(func.sum(VideoJob.provider_api_cost_usd)).scalar() or 0.0
    )
    estimated_compute = (
        db.query(func.sum(VideoJob.estimated_compute_cost_usd))
        .filter(VideoJob.compute_cost_known.is_(True))
        .scalar()
        or 0.0
    )
    unknown_compute_jobs = (
        db.query(func.count(VideoJob.id))
        .filter(
            VideoJob.status == JobStatus.COMPLETED.value,
            VideoJob.compute_cost_known.is_(False),
        )
        .scalar()
        or 0
    )

    decided = completed + failed
    return {
        "render_success_rate": (completed / decided) if decided else None,
        "provider_failure_rate": (failed / decided) if decided else None,
        "avg_time_to_first_frame_sec": _avg(VideoJob.time_to_first_frame_sec),
        "avg_time_to_ready_sec": _avg(VideoJob.render_time_sec),
        "avg_gpu_render_duration_sec": _avg(VideoJob.render_time_sec),
        "avg_queue_latency_sec": _avg(VideoJob.queue_latency_sec),
        "avg_cost_per_usable_asset_usd": (
            (float(provider_api_cost) + float(estimated_compute)) / ready if ready else None
        ),
        "qc_failure_rate": (qc_failed / decided) if decided else None,
        "publish_handoff_reliability": (published / ready) if ready else None,
        "engine_usage": engine_usage,
        "cost_breakdown": {
            "provider_api_cost_usd": float(provider_api_cost),
            "estimated_compute_cost_usd": float(estimated_compute),
            "unknown_compute_cost_jobs": int(unknown_compute_jobs),
            "note": "Self-hosted generation is not free compute; unknown when GPU-hour rate unset.",
        },
        "totals": {
            "jobs": int(total),
            "completed": int(completed),
            "failed": int(failed),
            "ready": int(ready),
            "published": int(published),
        },
    }

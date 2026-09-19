"""Async job orchestration: plan → route → render scenes → stitch → QC."""

from __future__ import annotations

import hashlib
import logging
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from gateway.audio import AudioPipeline
from gateway.captions import burn_captions, write_srt
from gateway.config import Settings, get_settings
from gateway.continuity import (
    build_continuity_metadata,
    extract_last_frame,
    link_scene_frame,
)
from gateway import database as dbmod
from gateway.metrics import record_engine_result
from gateway.models import JobStatus, VideoJob, utcnow
from gateway.qc import run_qc
from gateway.router import route_request
from gateway.scene_planner import build_scene_plan, scene_plan_to_dict
from gateway.schemas import VideoCreateRequest
from gateway.stitcher import ASPECT_RESOLUTIONS, normalize_social, stitch_clips
from gateway.storage import LocalStorage, get_storage
from gateway.worker_client import WorkerClient, summarize_worker

logger = logging.getLogger(__name__)


def new_job_id() -> str:
    return uuid.uuid4().hex


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def resolve_resolution(aspect_ratio: str, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    w, h = ASPECT_RESOLUTIONS.get(aspect_ratio, (1080, 1920))
    return f"{w}x{h}"


def effective_duration(req: VideoCreateRequest, settings: Settings) -> float:
    if req.allow_short:
        return float(req.duration)
    return max(float(req.duration), float(settings.min_social_duration))


def append_event(job: VideoJob, status: str, message: str, data: dict | None = None) -> None:
    events = list(job.events or [])
    events.append(
        {
            "ts": utcnow().isoformat(),
            "status": status,
            "message": message,
            "data": data or {},
        }
    )
    job.events = events
    job.status = status


def job_to_response(job: VideoJob) -> dict[str, Any]:
    qc_status = None
    if job.qc_result:
        qc_status = "passed" if job.qc_result.get("passed") else "failed"
    return {
        "job_id": job.id,
        "status": job.status,
        "asset_id": job.asset_id,
        "ready": bool(job.ready),
        "published": bool(job.published),
        "prompt": job.prompt,
        "duration": job.duration,
        "aspect_ratio": job.aspect_ratio,
        "resolution": job.resolution,
        "engine": job.engine_selected,
        "model": job.model,
        "qc_status": qc_status,
        "thumbnail": job.thumbnail_path,
        "output_location": job.output_path,
        "generation_timestamp": job.completed_at,
        "prompt_hash": job.prompt_hash,
        "content_hash": job.content_hash,
        "failure_reason": job.failure_reason,
        "failure_category": job.failure_category,
        "routing_decisions": job.routing_decisions or [],
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "output_duration": job.output_duration,
        "render_time_sec": job.render_time_sec,
    }


class JobService:
    def __init__(
        self,
        settings: Settings | None = None,
        storage: LocalStorage | None = None,
        worker: WorkerClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.storage = storage or get_storage(self.settings)
        self.worker = worker or WorkerClient(self.settings)
        self.audio = AudioPipeline()
        self._lock = threading.Lock()

    def create_job(self, db: Session, req: VideoCreateRequest) -> VideoJob:
        if req.idempotency_key:
            existing = (
                db.query(VideoJob)
                .filter(VideoJob.idempotency_key == req.idempotency_key)
                .one_or_none()
            )
            if existing:
                return existing

        duration = effective_duration(req, self.settings)
        job = VideoJob(
            id=new_job_id(),
            idempotency_key=req.idempotency_key,
            status=JobStatus.QUEUED.value,
            request_json=req.model_dump(),
            prompt=req.prompt,
            prompt_hash=prompt_hash(req.prompt),
            negative_prompt=req.negative_prompt,
            duration=duration,
            aspect_ratio=req.aspect_ratio,
            resolution=resolve_resolution(req.aspect_ratio, req.resolution),
            fps=req.fps,
            quality=req.quality,
            engine_requested=req.engine,
            seed=req.seed,
            audio_requested=req.audio,
            captions_requested=req.captions,
            events=[],
            routing_decisions=[],
        )
        append_event(job, JobStatus.QUEUED.value, "Job accepted")
        db.add(job)
        db.commit()
        db.refresh(job)
        self._spawn(job.id)
        return job

    def _spawn(self, job_id: str) -> None:
        t = threading.Thread(target=self._run_job, args=(job_id,), daemon=True)
        t.start()

    def cancel(self, db: Session, job_id: str) -> VideoJob | None:
        job = db.get(VideoJob, job_id)
        if not job:
            return None
        if job.status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value):
            return job
        job.cancelled = True
        append_event(job, JobStatus.CANCELLED.value, "Cancel requested")
        job.status = JobStatus.CANCELLED.value
        job.completed_at = utcnow()
        db.commit()
        try:
            self.worker.cancel(job_id)
        except Exception:  # noqa: BLE001
            pass
        return job

    def _run_job(self, job_id: str) -> None:
        if dbmod.SessionLocal is None:
            dbmod.configure_engine()
        assert dbmod.SessionLocal is not None
        db = dbmod.SessionLocal()
        try:
            self._execute(db, job_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("job %s crashed", job_id)
            job = db.get(VideoJob, job_id)
            if job and job.status not in (JobStatus.COMPLETED.value, JobStatus.CANCELLED.value):
                job.failure_reason = str(exc)
                job.failure_category = "internal_error"
                append_event(job, JobStatus.FAILED.value, f"Internal error: {exc}")
                job.completed_at = utcnow()
                db.commit()
        finally:
            db.close()

    def _execute(self, db: Session, job_id: str) -> None:
        job = db.get(VideoJob, job_id)
        if not job:
            return
        if job.cancelled:
            return

        job.started_at = utcnow()
        job.queue_latency_sec = (job.started_at - job.created_at).total_seconds()
        req = VideoCreateRequest.model_validate(job.request_json)

        # PLANNING
        append_event(job, JobStatus.PLANNING.value, "Building scene plan")
        db.commit()
        plan = build_scene_plan(
            job.prompt,
            duration=job.duration,
            aspect_ratio=job.aspect_ratio,
            negative_prompt=job.negative_prompt,
            seed=job.seed,
            style_lock=req.style_lock,
            character_lock=req.character_lock,
            environment_lock=req.environment_lock,
        )
        job.scene_plan = scene_plan_to_dict(plan)
        job.continuity = build_continuity_metadata(
            style_lock=plan.style_lock,
            character_lock=plan.character_lock,
            environment_lock=plan.environment_lock,
            base_seed=job.seed,
            scene_count=len(plan.scenes),
        )
        db.commit()

        if job.cancelled:
            return

        # ROUTING
        append_event(job, JobStatus.ROUTING.value, "Selecting open engine")
        db.commit()
        worker_health = self.worker.health()
        summary = summarize_worker(worker_health)
        decision = route_request(
            mode=req.engine,
            quality=req.quality,
            duration=job.duration,
            has_image=bool(req.input_image),
            has_video=bool(req.input_video),
            preferred_engine=req.engine if req.engine in ("wan", "ltx", "framepack") else None,
            ready_engines=summary["ready_engines"],
            installed_engines=summary["installed_engines"],
            vram_gb=(summary["vram_total_mb"] / 1024.0) if summary.get("vram_total_mb") else None,
            db=db,
        )
        decisions = list(job.routing_decisions or [])
        decisions.append(
            {
                "ts": utcnow().isoformat(),
                "engine": decision.engine,
                "reason": decision.reason,
                "scores": decision.scores,
                "candidates": decision.candidates,
                "rejected": decision.rejected,
                "mode": decision.mode,
                "generation_available": summary["generation_available"],
            }
        )
        job.routing_decisions = decisions
        job.engine_selected = decision.engine
        db.commit()

        if not summary["generation_available"] or not decision.engine:
            job.failure_category = "generation_unavailable"
            job.failure_reason = (
                "GENERATION BLOCKED — NO COMPATIBLE GPU WORKER"
                if not summary["gpu_worker_available"]
                else f"No ready open engine: {decision.reason}"
            )
            append_event(job, JobStatus.FAILED.value, job.failure_reason, {"routing": decision.reason})
            job.completed_at = utcnow()
            db.commit()
            return

        job_dir = self.storage.job_dir(job.id)
        temp_dir = self.storage.temp_dir(job.id)
        scene_clips: list[Path] = []
        tried: set[str] = set()
        last_error: str | None = None
        render_started = utcnow()

        append_event(job, JobStatus.RENDERING.value, f"Rendering with {decision.engine}")
        db.commit()

        engines_to_try = [decision.engine] + [
            e for e in decision.candidates if e != decision.engine
        ]

        for engine in engines_to_try:
            if job.cancelled:
                return
            if engine in tried:
                continue
            tried.add(engine)
            job.engine_selected = engine
            job.attempts += 1
            decisions = list(job.routing_decisions or [])
            decisions.append(
                {
                    "ts": utcnow().isoformat(),
                    "action": "attempt",
                    "engine": engine,
                    "attempt": job.attempts,
                }
            )
            job.routing_decisions = decisions
            db.commit()

            try:
                scene_clips = self._render_scenes(
                    db,
                    job,
                    req,
                    engine=engine,
                    temp_dir=temp_dir,
                )
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning("engine %s failed for job %s: %s", engine, job.id, exc)
                decisions = list(job.routing_decisions or [])
                decisions.append(
                    {
                        "ts": utcnow().isoformat(),
                        "action": "fallback",
                        "failed_engine": engine,
                        "error": last_error,
                    }
                )
                job.routing_decisions = decisions
                record_engine_result(db, engine, success=False)
                db.commit()
                scene_clips = []

        if not scene_clips:
            job.failure_category = "all_engines_failed"
            job.failure_reason = last_error or "All open engines failed"
            append_event(job, JobStatus.FAILED.value, job.failure_reason)
            job.completed_at = utcnow()
            db.commit()
            return

        if job.cancelled:
            return

        # STITCHING
        append_event(job, JobStatus.STITCHING.value, "Stitching scenes")
        db.commit()
        stitched = temp_dir / "stitched.mp4"
        stitch_clips(
            scene_clips,
            stitched,
            transition=req.transition,
            fps=job.fps,
        )
        normalized = temp_dir / "normalized.mp4"
        w, h = ASPECT_RESOLUTIONS.get(job.aspect_ratio, (1080, 1920))
        normalize_social(stitched, normalized, aspect_ratio=job.aspect_ratio, fps=job.fps)

        current = normalized

        # AUDIO (optional — never fail visuals for missing TTS)
        if job.audio_requested:
            append_event(job, JobStatus.AUDIO.value, "Audio pipeline")
            db.commit()
            try:
                vo = self.audio.build_voiceover_track(
                    [
                        {
                            "text": s.voiceover,
                            "start": s.start,
                            "duration": s.duration,
                        }
                        for s in plan.scenes
                    ],
                    temp_dir / "voiceover.m4a",
                )
                if vo:
                    mixed = temp_dir / "with_audio.mp4"
                    self.audio.mix(current, mixed, voiceover=vo)
                    current = mixed
                else:
                    append_event(
                        job,
                        JobStatus.AUDIO.value,
                        "TTS unavailable — continuing without voiceover",
                    )
                    db.commit()
            except Exception as exc:  # noqa: BLE001
                append_event(
                    job,
                    JobStatus.AUDIO.value,
                    f"Audio skipped: {exc}",
                )
                db.commit()

        # CAPTIONS
        if job.captions_requested:
            try:
                srt_path = write_srt(
                    [s.model_dump() for s in plan.scenes],
                    job_dir / "captions.srt",
                )
                captioned = temp_dir / "captioned.mp4"
                burn_captions(current, srt_path, captioned)
                current = captioned
            except Exception as exc:  # noqa: BLE001
                append_event(job, job.status, f"Captions skipped: {exc}")
                db.commit()

        # QC
        append_event(job, JobStatus.QC.value, "Running quality control")
        db.commit()
        final_key = f"{job.id}/final.mp4"
        final_path = self.storage.get_path(final_key, category="outputs")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(current.read_bytes())

        qc = run_qc(
            final_path,
            expected_duration=job.duration,
            expected_width=w,
            expected_height=h,
            expected_fps=float(job.fps),
            require_audio=False,  # audio optional even when requested if TTS missing
            min_duration=job.duration - 1.5,
        )
        job.qc_result = qc
        if not qc.get("passed"):
            job.failure_category = "qc_failed"
            job.failure_reason = f"QC failed: {', '.join(qc.get('failures') or [])}"
            append_event(job, JobStatus.FAILED.value, job.failure_reason, {"qc": qc})
            job.completed_at = utcnow()
            job.ready = False
            if job.engine_selected:
                record_engine_result(
                    db,
                    job.engine_selected,
                    success=False,
                    qc_failed=True,
                )
            db.commit()
            return

        # COMPLETED
        job.output_path = str(final_path)
        job.output_duration = float(qc["checks"].get("duration") or 0)
        job.content_hash = self.storage.file_hash(final_path)
        job.asset_id = f"asset_{job.id[:16]}"
        job.ready = True
        job.published = False
        job.render_time_sec = (utcnow() - render_started).total_seconds()
        # Estimated compute cost (not zero for self-hosted)
        hours = (job.render_time_sec or 0) / 3600.0
        job.estimated_compute_cost_usd = hours * self.settings.compute_cost_per_gpu_hour_usd
        job.compute_cost_known = True
        job.provider_api_cost_usd = 0.0
        job.completed_at = utcnow()
        append_event(job, JobStatus.COMPLETED.value, "Asset READY after QC")
        if job.engine_selected:
            record_engine_result(
                db,
                job.engine_selected,
                success=True,
                render_time_sec=job.render_time_sec or 0,
                queue_latency_sec=job.queue_latency_sec or 0,
            )
        # Thumbnail
        try:
            thumb = self.storage.get_path(f"{job.id}/thumb.jpg", category="thumbnails")
            thumb.parent.mkdir(parents=True, exist_ok=True)
            extract_last_frame(final_path, thumb)
            if thumb.exists():
                job.thumbnail_path = str(thumb)
        except Exception:  # noqa: BLE001
            pass
        db.commit()

    def _render_scenes(
        self,
        db: Session,
        job: VideoJob,
        req: VideoCreateRequest,
        *,
        engine: str,
        temp_dir: Path,
    ) -> list[Path]:
        plan = job.scene_plan or {}
        scenes = plan.get("scenes") or []
        clips: list[Path] = []
        conditioning: str | None = req.input_image
        continuity = dict(job.continuity or {})

        for scene in scenes:
            if job.cancelled:
                raise RuntimeError("cancelled")
            idx = int(scene["index"])
            out = temp_dir / f"scene_{idx:03d}.mp4"
            payload = {
                "job_id": job.id,
                "scene_index": idx,
                "engine": engine,
                "task": "image-to-video" if conditioning else "text-to-video",
                "prompt": scene["visual_prompt"],
                "negative_prompt": scene.get("negative_prompt") or job.negative_prompt,
                "duration": float(scene["duration"]),
                "aspect_ratio": job.aspect_ratio,
                "resolution": job.resolution,
                "fps": job.fps,
                "seed": scene.get("seed"),
                "quality": job.quality,
                "input_image": conditioning,
                "input_video": req.input_video if idx == 0 else None,
                "low_vram": req.engine == "low_vram" or job.quality == "draft",
                "output_path": str(out),
            }
            append_event(
                job,
                JobStatus.RENDERING.value,
                f"Scene {idx + 1}/{len(scenes)} via {engine}",
                {"scene": idx, "engine": engine},
            )
            db.commit()

            result = self.worker.generate(payload)
            if result.get("status") == "downloading_model":
                append_event(job, JobStatus.DOWNLOADING_MODEL.value, "Worker downloading model")
                db.commit()
            if not result.get("ok"):
                raise RuntimeError(result.get("error") or "worker generate failed")
            produced = Path(result.get("output_path") or out)
            if not produced.exists():
                raise RuntimeError(f"worker returned missing output: {produced}")
            clips.append(produced)
            if result.get("model"):
                job.model = result["model"]
            if result.get("peak_vram_mb"):
                job.peak_vram_mb = float(result["peak_vram_mb"])
            if result.get("time_to_first_frame_sec") and job.time_to_first_frame_sec is None:
                job.time_to_first_frame_sec = float(result["time_to_first_frame_sec"])

            # Continuity: final frame → next I2V
            frame_path = temp_dir / f"scene_{idx:03d}_last.jpg"
            extracted = extract_last_frame(produced, frame_path)
            if extracted:
                conditioning = str(extracted)
                continuity = link_scene_frame(
                    continuity,
                    from_scene=idx,
                    to_scene=idx + 1,
                    frame_path=str(extracted),
                )
                job.continuity = continuity
            db.commit()

        return clips

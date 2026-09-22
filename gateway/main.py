"""FastAPI master video gateway — control plane only."""

from __future__ import annotations

import logging
import mimetypes
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from gateway import __version__
from gateway.auth import require_api_key
from gateway.config import Settings, get_settings
from gateway.database import get_db, init_db
from gateway.jobs import JobService, job_to_response
from gateway.metrics import collect_metrics
from gateway.models import JobStatus, VideoJob
from gateway.router import ENGINE_CATALOG, providers_snapshot
from gateway.schemas import (
    HealthResponse,
    MetricsResponse,
    ProviderInfo,
    VideoCreateRequest,
    VideoCreateResponse,
    VideoJobResponse,
)
from gateway.storage import get_storage
from gateway.worker_client import WorkerClient, summarize_worker

logger = logging.getLogger(__name__)

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "video"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    init_db()
    app.state.job_service = JobService(settings)
    app.state.worker_client = WorkerClient(settings)
    logger.info("Video gateway %s started", __version__)
    yield


app = FastAPI(
    title="Video Creation Gateway",
    description="Master AI video generation control plane for Instagram/YouTube automation.",
    version=__version__,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if DASHBOARD_DIR.exists():
    app.mount("/dashboard/assets", StaticFiles(directory=str(DASHBOARD_DIR)), name="dashboard_assets")


def _svc(request: Request) -> JobService:
    return request.app.state.job_service


def _worker(request: Request) -> WorkerClient:
    return request.app.state.worker_client


@app.get("/health", response_model=HealthResponse)
def health(
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
    worker: Annotated[WorkerClient, Depends(_worker)],
) -> HealthResponse:
    wh = worker.health()
    summary = summarize_worker(wh)
    queue_depth = (
        db.query(VideoJob)
        .filter(
            VideoJob.status.in_(
                [
                    JobStatus.QUEUED.value,
                    JobStatus.PLANNING.value,
                    JobStatus.ROUTING.value,
                    JobStatus.DOWNLOADING_MODEL.value,
                    JobStatus.RENDERING.value,
                    JobStatus.STITCHING.value,
                    JobStatus.AUDIO.value,
                    JobStatus.QC.value,
                ]
            )
        )
        .count()
    )
    gen = bool(summary["generation_available"])
    return HealthResponse(
        status="ok" if True else "degraded",
        gateway="ok",
        generation_available=gen,
        gpu_worker_available=bool(summary["gpu_worker_available"]),
        installed_engines=summary["installed_engines"],
        ready_engines=summary["ready_engines"],
        model_loading=summary["model_loading"],
        queue_depth=queue_depth,
        version=__version__,
        worker=wh,
        details={
            "worker_configured": worker.configured,
            "default_engine": settings.default_engine,
            "auth_required": settings.auth_required,
            "cuda_available": summary.get("cuda_available"),
            "vram_total_mb": summary.get("vram_total_mb"),
            "vram_free_mb": summary.get("vram_free_mb"),
        },
    )


_GPU_READINESS_TEST_IDEMPOTENCY_KEY = "internal-gpu-readiness-test-render-v1"
_GPU_READINESS_TEST_PROMPT = (
    "A cinematic product-style technology scene showing an abstract AI compute "
    "core coming online, subtle flowing light, premium dark studio environment, "
    "smooth controlled camera movement, highly detailed, realistic lighting, "
    "no text, no logos, no people."
)


def _create_video_job(
    db: Session,
    svc: JobService,
    body: VideoCreateRequest,
) -> VideoJob:
    """Shared job-creation path for POST /v1/videos and temporary internal probes."""
    return svc.create_job(db, body)


def _sanitized_gpu_readiness(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "cuda_available": summary["cuda_available"],
        "ready_engines": summary["ready_engines"],
        "model_loading": summary["model_loading"],
        "vram_total_mb": summary["vram_total_mb"],
        "vram_free_mb": summary["vram_free_mb"],
    }


@app.get("/internal/gpu-readiness")
def gpu_readiness(
    _: Annotated[str, Depends(require_api_key)],
    worker: Annotated[WorkerClient, Depends(_worker)],
) -> dict[str, Any]:
    """Temporary sanitized GPU readiness probe; never returns worker secrets."""
    summary = summarize_worker(worker.health())
    return _sanitized_gpu_readiness(summary)


@app.post("/internal/gpu-readiness-test-render")
def gpu_readiness_test_render(
    _: Annotated[str, Depends(require_api_key)],
    worker: Annotated[WorkerClient, Depends(_worker)],
    db: Annotated[Session, Depends(get_db)],
    svc: Annotated[JobService, Depends(_svc)],
) -> dict[str, Any]:
    """Temporary readiness-gated GPU test render; never returns worker secrets."""
    summary = summarize_worker(worker.health())
    readiness = _sanitized_gpu_readiness(summary)

    if not summary["cuda_available"] or not summary["ready_engines"]:
        return {
            **readiness,
            "submitted": False,
            "job_id": None,
            "status": "not_submitted",
        }

    body = VideoCreateRequest(
        prompt=_GPU_READINESS_TEST_PROMPT,
        duration=30.0,
        aspect_ratio="9:16",
        engine="auto",
        idempotency_key=_GPU_READINESS_TEST_IDEMPOTENCY_KEY,
    )
    job = _create_video_job(db, svc, body)
    return {
        **readiness,
        "submitted": True,
        "job_id": str(job.id),
        "status": job.status,
    }


@app.get("/version")
def version() -> dict[str, str]:
    return {"version": __version__, "service": "video-gateway"}


@app.get("/v1/providers", response_model=list[ProviderInfo])
def list_providers(
    _: Annotated[str, Depends(require_api_key)],
    worker: Annotated[WorkerClient, Depends(_worker)],
) -> list[ProviderInfo]:
    wh = worker.health()
    summary = summarize_worker(wh)
    rows = providers_snapshot(
        installed=set(summary["installed_engines"]),
        ready=set(summary["ready_engines"]),
        gpu_available=bool(summary.get("cuda_available")),
        details={name: ENGINE_CATALOG[name] for name in ENGINE_CATALOG},
    )
    return [ProviderInfo(**r) for r in rows]


@app.get("/v1/providers/health")
def providers_health(
    _: Annotated[str, Depends(require_api_key)],
    worker: Annotated[WorkerClient, Depends(_worker)],
) -> dict[str, Any]:
    wh = worker.health()
    summary = summarize_worker(wh)
    return {
        "generation_available": summary["generation_available"],
        "gpu_worker_available": summary["gpu_worker_available"],
        "providers": providers_snapshot(
            installed=set(summary["installed_engines"]),
            ready=set(summary["ready_engines"]),
            gpu_available=bool(summary.get("cuda_available")),
        ),
        "worker": wh,
    }


@app.post("/v1/videos", response_model=VideoCreateResponse)
def create_video(
    body: VideoCreateRequest,
    _: Annotated[str, Depends(require_api_key)],
    db: Annotated[Session, Depends(get_db)],
    svc: Annotated[JobService, Depends(_svc)],
) -> VideoCreateResponse:
    job = _create_video_job(db, svc, body)
    return VideoCreateResponse(job_id=job.id, status=job.status)


@app.get("/v1/videos/{job_id}", response_model=VideoJobResponse)
def get_video(
    job_id: str,
    _: Annotated[str, Depends(require_api_key)],
    db: Annotated[Session, Depends(get_db)],
) -> VideoJobResponse:
    job = db.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return VideoJobResponse(**job_to_response(job))


@app.get("/v1/videos/{job_id}/events")
def get_events(
    job_id: str,
    _: Annotated[str, Depends(require_api_key)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    job = db.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return {"job_id": job.id, "status": job.status, "events": job.events or []}


@app.post("/v1/videos/{job_id}/cancel", response_model=VideoJobResponse)
def cancel_video(
    job_id: str,
    _: Annotated[str, Depends(require_api_key)],
    db: Annotated[Session, Depends(get_db)],
    svc: Annotated[JobService, Depends(_svc)],
) -> VideoJobResponse:
    job = svc.cancel(db, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return VideoJobResponse(**job_to_response(job))


@app.get("/v1/videos/{job_id}/download")
def download_video(
    job_id: str,
    _: Annotated[str, Depends(require_api_key)],
    db: Annotated[Session, Depends(get_db)],
):
    job = db.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if not job.ready or not job.output_path:
        raise HTTPException(409, "asset not READY — QC must pass before download")
    path = Path(job.output_path)
    if not path.exists():
        raise HTTPException(404, "output file missing")
    return FileResponse(path, media_type="video/mp4", filename=f"{job_id}.mp4")


@app.get("/v1/videos/{job_id}/preview")
def preview_video(
    job_id: str,
    _: Annotated[str, Depends(require_api_key)],
    db: Annotated[Session, Depends(get_db)],
):
    """Inline MP4 preview for READY assets — same readiness gate as download."""
    job = db.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if not job.ready or not job.output_path:
        raise HTTPException(409, "asset not READY — QC must pass before preview")
    path = Path(job.output_path)
    if not path.exists():
        raise HTTPException(404, "output file missing")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"{job_id}.mp4",
        content_disposition_type="inline",
    )


@app.get("/v1/videos/{job_id}/thumbnail")
def video_thumbnail(
    job_id: str,
    _: Annotated[str, Depends(require_api_key)],
    db: Annotated[Session, Depends(get_db)],
):
    job = db.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if not job.thumbnail_path:
        raise HTTPException(404, "thumbnail not available")
    path = Path(job.thumbnail_path)
    if not path.exists():
        raise HTTPException(404, "thumbnail file missing")
    media = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    return FileResponse(path, media_type=media)


_UPLOAD_MAX_BYTES = 25 * 1024 * 1024
_UPLOAD_ALLOWED = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _safe_upload_name(name: str | None) -> str:
    base = Path(name or "upload").name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "upload"
    return base[:80]


@app.post("/v1/assets/upload")
async def upload_asset(
    _: Annotated[str, Depends(require_api_key)],
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Store a reference image for image-to-video. Returns a server path for input_image."""
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in _UPLOAD_ALLOWED:
        raise HTTPException(415, "Only JPEG, PNG, WebP, or GIF images are accepted")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty upload")
    if len(data) > _UPLOAD_MAX_BYTES:
        raise HTTPException(413, "Image exceeds 25MB limit")

    storage = get_storage()
    asset_id = uuid.uuid4().hex[:16]
    ext = _UPLOAD_ALLOWED[content_type]
    original = _safe_upload_name(file.filename)
    key = f"{asset_id}_{original}"
    if not key.lower().endswith(ext):
        key = f"{key}{ext}"
    dest = storage.get_path(key, category="uploads")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return {
        "asset_id": asset_id,
        "filename": original,
        "content_type": content_type,
        "size_bytes": len(data),
        "path": str(dest),
        "kind": "uploaded_image",
    }


@app.get("/v1/assets")
def list_assets(
    _: Annotated[str, Depends(require_api_key)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    limit: int = Query(100, ge=1, le=200),
) -> dict[str, Any]:
    """List real uploaded images and READY generated videos — never invents assets."""
    storage = get_storage(settings)
    uploads_dir = storage.root / "uploads"
    assets: list[dict[str, Any]] = []

    if uploads_dir.exists():
        for path in sorted(uploads_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not path.is_file():
                continue
            assets.append(
                {
                    "id": path.stem.split("_", 1)[0],
                    "kind": "uploaded_image",
                    "filename": path.name,
                    "path": str(path),
                    "size_bytes": path.stat().st_size,
                    "created_at": path.stat().st_mtime,
                    "job_id": None,
                    "ready": True,
                }
            )

    jobs = (
        db.query(VideoJob)
        .filter(VideoJob.ready.is_(True))
        .order_by(VideoJob.completed_at.desc())
        .limit(limit)
        .all()
    )
    for job in jobs:
        assets.append(
            {
                "id": job.asset_id or job.id,
                "kind": "generated_video",
                "filename": f"{job.id}.mp4",
                "path": None,  # never expose output filesystem path in list
                "size_bytes": None,
                "created_at": (job.completed_at or job.created_at).isoformat()
                if (job.completed_at or job.created_at)
                else None,
                "job_id": job.id,
                "ready": True,
                "thumbnail_available": bool(job.thumbnail_path),
                "prompt": (job.prompt or "")[:160],
                "engine": job.engine_selected,
                "duration": job.output_duration or job.duration,
                "aspect_ratio": job.aspect_ratio,
            }
        )

    return {"assets": assets[:limit], "count": min(len(assets), limit)}


@app.get("/v1/assets/{asset_id}/file")
def get_uploaded_asset_file(
    asset_id: str,
    _: Annotated[str, Depends(require_api_key)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    """Serve an uploaded reference image by asset id (no path traversal)."""
    if not re.fullmatch(r"[a-f0-9]{8,32}", asset_id):
        raise HTTPException(400, "invalid asset id")
    storage = get_storage(settings)
    uploads_dir = storage.root / "uploads"
    if not uploads_dir.exists():
        raise HTTPException(404, "asset not found")
    matches = list(uploads_dir.glob(f"{asset_id}_*"))
    if not matches:
        raise HTTPException(404, "asset not found")
    path = matches[0]
    media = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(path, media_type=media)


@app.get("/v1/jobs")
def list_jobs(
    _: Annotated[str, Depends(require_api_key)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(50, ge=1, le=200),
    status: str | None = None,
) -> dict[str, Any]:
    q = db.query(VideoJob).order_by(VideoJob.created_at.desc())
    if status:
        q = q.filter(VideoJob.status == status)
    jobs = q.limit(limit).all()
    return {"jobs": [job_to_response(j) for j in jobs], "count": len(jobs)}


@app.get("/v1/metrics", response_model=MetricsResponse)
def metrics(
    _: Annotated[str, Depends(require_api_key)],
    db: Annotated[Session, Depends(get_db)],
) -> MetricsResponse:
    return MetricsResponse(**collect_metrics(db))


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_index() -> HTMLResponse:
    index = DASHBOARD_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Video Gateway</h1><p>Dashboard not found.</p>")


def create_app() -> FastAPI:
    return app

"""FastAPI master video gateway — control plane only."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
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
    job = svc.create_job(db, body)
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

"""GPU worker FastAPI service — internal token protected."""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status

from worker import __version__
from worker.config import WorkerSettings, get_worker_settings
from worker.inference import InferenceService
from worker.model_manager import ModelManager

logger = logging.getLogger(__name__)


def require_worker_token(
    settings: Annotated[WorkerSettings, Depends(get_worker_settings)],
    x_worker_token: Annotated[str | None, Header()] = None,
) -> str:
    if not settings.worker_token:
        # Fail closed in non-mock deployments when token unset and mock off.
        if not settings.allow_mock_inference:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="WORKER_TOKEN not configured",
            )
        return "dev"
    if not x_worker_token or not secrets.compare_digest(x_worker_token, settings.worker_token):
        raise HTTPException(status_code=401, detail="Invalid worker token")
    return x_worker_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_worker_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    manager = ModelManager(settings)
    app.state.settings = settings
    app.state.manager = manager
    app.state.inference = InferenceService(manager, settings)
    logger.info("Video worker %s started (mock=%s)", __version__, settings.allow_mock_inference)
    yield


app = FastAPI(title="Video GPU Worker", version=__version__, lifespan=lifespan)


def _safe_readiness(manager: ModelManager, settings: WorkerSettings) -> dict[str, Any]:
    payload = manager.health_payload()
    # Never claim generation_available for mock-only unless explicitly allowed —
    # mock is for automated tests, not production readiness.
    if settings.allow_mock_inference and not payload["cuda_available"]:
        payload["generation_available"] = False
        payload["mock_only"] = True
        payload["note"] = (
            "ALLOW_MOCK_INFERENCE set but generation_available remains false without CUDA"
        )
    return {
        "worker_available": True,
        "cuda_available": bool(payload.get("cuda_available")),
        "installed_engines": list(payload.get("installed_engines") or []),
        "ready_engines": list(payload.get("ready_engines") or []),
        "model_loading": dict(payload.get("model_loading") or {}),
        "queue_depth": 0,
        "vram_total_mb": payload.get("vram_total_mb"),
        "vram_free_mb": payload.get("vram_free_mb"),
        "generation_available": bool(payload.get("generation_available")),
    }


@app.get("/health")
def health(request: Request) -> dict[str, Any]:
    """Liveness: process is up. Does NOT imply generation readiness."""
    settings: WorkerSettings = request.app.state.settings
    manager: ModelManager = request.app.state.manager
    payload = manager.health_payload()
    if settings.allow_mock_inference and not payload["cuda_available"]:
        payload["generation_available"] = False
        payload["mock_only"] = True
        payload["note"] = (
            "ALLOW_MOCK_INFERENCE set but generation_available remains false without CUDA"
        )
    return {
        "ok": True,
        "worker_available": True,
        "worker_id": settings.worker_id,
        "version": __version__,
        "queue_depth": 0,
        **payload,
    }


@app.get("/ready")
def ready(request: Request) -> dict[str, Any]:
    """Readiness: which engines can actually generate right now."""
    settings: WorkerSettings = request.app.state.settings
    manager: ModelManager = request.app.state.manager
    return _safe_readiness(manager, settings)


@app.get("/models")
def models(
    request: Request,
    _: Annotated[str, Depends(require_worker_token)],
) -> dict[str, Any]:
    manager: ModelManager = request.app.state.manager
    return {
        "models": manager.list_models(),
        "installed_engines": manager.installed_engines(),
        "ready_engines": manager.ready_engines(),
    }


@app.post("/generate")
def generate(
    body: dict[str, Any],
    request: Request,
    _: Annotated[str, Depends(require_worker_token)],
) -> dict[str, Any]:
    """Run generation. Always returns JSON — never blank 502 from uncaught app errors."""
    inference: InferenceService = request.app.state.inference
    try:
        return inference.generate(body)
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate endpoint failed")
        return {
            "ok": False,
            "error": str(exc)[:500],
            "error_category": "worker_internal_error",
            "engine": body.get("engine") if isinstance(body, dict) else None,
        }


@app.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    request: Request,
    _: Annotated[str, Depends(require_worker_token)],
) -> dict[str, Any]:
    inference: InferenceService = request.app.state.inference
    # Return any scene jobs matching prefix
    matches = {k: v for k, v in inference._jobs.items() if k.startswith(job_id)}
    if not matches:
        raise HTTPException(404, "job not found")
    return {"job_id": job_id, "scenes": matches}


@app.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    request: Request,
    _: Annotated[str, Depends(require_worker_token)],
) -> dict[str, Any]:
    inference: InferenceService = request.app.state.inference
    return inference.cancel(job_id)


def create_app() -> FastAPI:
    return app

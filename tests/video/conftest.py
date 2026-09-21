"""Video platform test fixtures — mock worker is TEST ONLY."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'gw.db'}")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("WORKER_TOKEN", "test-worker-token")
    monkeypatch.setenv("VIDEO_WORKER_URL", "")
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED", "false")
    monkeypatch.setenv("ALLOW_MOCK_INFERENCE", "false")
    # Avoid OpenCV native teardown aborts when job threads exit during pytest shutdown.
    monkeypatch.setenv("GATEWAY_QC_SKIP_OPENCV", "1")

    from gateway.config import get_settings

    get_settings.cache_clear()

    import gateway.database as dbmod
    from gateway.database import Base
    from gateway.main import app

    dbmod.configure_engine()
    Base.metadata.drop_all(bind=dbmod.engine)
    Base.metadata.create_all(bind=dbmod.engine)

    from gateway.jobs import JobService
    from gateway.worker_client import WorkerClient

    worker = WorkerClient(get_settings())
    svc = JobService(get_settings(), worker=worker)
    with TestClient(app) as c:
        c.app.state.job_service = svc
        c.app.state.worker_client = worker
        yield c
        # Drain background job threads before the interpreter tears down native libs.
        try:
            svc.drain(timeout=90.0)
        except Exception:  # noqa: BLE001
            pass

    get_settings.cache_clear()


@pytest.fixture()
def auth_headers():
    return {"X-API-Key": "test-api-key"}

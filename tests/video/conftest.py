"""Video platform test fixtures — mock worker is TEST ONLY."""

from __future__ import annotations

import os
from pathlib import Path

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

    from gateway.config import get_settings

    get_settings.cache_clear()

    import gateway.database as dbmod
    from gateway.database import Base
    from gateway.main import app

    dbmod.configure_engine()
    Base.metadata.drop_all(bind=dbmod.engine)
    Base.metadata.create_all(bind=dbmod.engine)

    # Refresh app-bound services with new settings
    from gateway.jobs import JobService
    from gateway.worker_client import WorkerClient

    with TestClient(app) as c:
        c.app.state.job_service = JobService(get_settings())
        c.app.state.worker_client = WorkerClient(get_settings())
        yield c

    get_settings.cache_clear()


@pytest.fixture()
def auth_headers():
    return {"X-API-Key": "test-api-key"}

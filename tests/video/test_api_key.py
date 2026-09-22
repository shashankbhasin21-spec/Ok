"""API key usability and fail-closed auth."""

from __future__ import annotations

import pytest

from gateway.auth import WEAK_API_KEYS, is_usable_api_key


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, False),
        ("", False),
        ("   ", False),
        ("changeme", False),
        ("change-me-gateway-key", False),
        ("your-key", False),
        ("test-api-key", True),
        ("x" * 32, True),
    ],
)
def test_is_usable_api_key(value, expected):
    assert is_usable_api_key(value) is expected


def test_weak_set_covers_compose_placeholders():
    assert "changeme" in WEAK_API_KEYS
    assert "change-me-gateway-key" in WEAK_API_KEYS


def test_placeholder_api_key_fails_closed(tmp_path, monkeypatch):
    """AUTH_MODE=required with placeholder API_KEY must not open the API."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'gw.db'}")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("API_KEY", "changeme")
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
    from fastapi.testclient import TestClient

    dbmod.configure_engine()
    Base.metadata.drop_all(bind=dbmod.engine)
    Base.metadata.create_all(bind=dbmod.engine)

    with TestClient(app) as c:
        r = c.get("/v1/providers")
        assert r.status_code == 503
        assert "API_KEY not configured" in r.json()["detail"]

    get_settings.cache_clear()

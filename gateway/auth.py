"""API key authentication for the video gateway."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from gateway.config import Settings, get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Placeholders from examples / docker defaults — treat as unconfigured.
WEAK_API_KEYS = frozenset(
    {
        "changeme",
        "change-me-gateway-key",
        "change-me",
        "your-key",
        "your-api-key",
        "api-key",
        "secret",
        "password",
    }
)


def is_usable_api_key(api_key: str | None) -> bool:
    """True when the configured server key is non-empty and not a known placeholder."""
    if not api_key or not api_key.strip():
        return False
    return api_key.strip() not in WEAK_API_KEYS


def require_api_key(
    api_key: Annotated[str | None, Security(api_key_header)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    if not settings.auth_required:
        return api_key or "dev"
    if not is_usable_api_key(settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API_KEY not configured — gateway refused to start open",
        )
    if not api_key or not secrets.compare_digest(api_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return api_key

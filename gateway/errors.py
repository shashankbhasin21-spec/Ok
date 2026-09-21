"""Sanitized failure classification for worker / generation errors."""

from __future__ import annotations

import re
from typing import Any


# Patterns that must never appear in logs or client-facing failure_reason.
_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|worker[_-]?token|authorization|bearer)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(x-api-key|x-worker-token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)https?://[^\s]*[?&](token|signature|sig|key)=[^\s&]+"),
    re.compile(r"(?i)(hf_token|password|secret)\s*[:=]\s*\S+"),
]


def sanitize_error_text(text: str | None, *, limit: int = 400) -> str:
    if not text:
        return ""
    cleaned = str(text)
    for pat in _SECRET_PATTERNS:
        cleaned = pat.sub("[redacted]", cleaned)
    # Drop absolute filesystem roots that may leak host layout.
    cleaned = re.sub(r"(?i)(/[\w.-]+){3,}/\S+", "[path]", cleaned)
    return cleaned[:limit]


def classify_worker_http_error(status_code: int, body: str | None = None) -> tuple[str, str]:
    """Map worker HTTP failures to stable failure_category + sanitized message."""
    safe = sanitize_error_text(body)
    if status_code == 502:
        return (
            "worker_bad_gateway",
            "worker generate failed: upstream returned HTTP 502 "
            "(process crash, proxy timeout, or engine OOM) "
            + (f"— {safe}" if safe else ""),
        )
    if status_code == 504:
        return ("worker_timeout", f"worker generate timed out (HTTP 504) {safe}".strip())
    if status_code == 503:
        return ("engine_not_ready", f"worker unavailable (HTTP 503) {safe}".strip())
    if status_code == 401:
        return ("worker_auth_failed", "worker authentication failed")
    if status_code >= 500:
        return (
            "worker_internal_error",
            f"worker generate failed: HTTP {status_code} {safe}".strip(),
        )
    return (
        "generation_failed",
        f"worker generate failed: HTTP {status_code} {safe}".strip(),
    )


def classify_exception(exc: BaseException) -> tuple[str, str]:
    msg = sanitize_error_text(str(exc))
    low = msg.lower()
    if "502" in low or "bad gateway" in low:
        return "worker_bad_gateway", msg or "worker bad gateway"
    if "timeout" in low or "timed out" in low or "504" in low:
        return "worker_timeout", msg or "worker timeout"
    if "cuda" in low and ("unavail" in low or "not available" in low):
        return "cuda_unavailable", msg or "CUDA unavailable"
    if "vram" in low or "out of memory" in low or "oom" in low:
        return "insufficient_vram", msg or "insufficient VRAM"
    if "not ready" in low or "engine_not_ready" in low:
        return "engine_not_ready", msg or "engine not ready"
    if "model_not" in low or "not installed" in low or "not loaded" in low:
        return "model_not_loaded", msg or "model not loaded"
    if "ffmpeg" in low or "stitch" in low:
        return "stitching_failed", msg or "stitching failed"
    if "qc" in low:
        return "qc_failed", msg or "QC failed"
    if "cancelled" in low:
        return "cancelled", msg or "cancelled"
    return "generation_failed", msg or "generation failed"


def readiness_fields(health: dict[str, Any] | None) -> dict[str, Any]:
    """Safe operational readiness fields — never secrets."""
    if not health:
        return {
            "worker_available": False,
            "cuda_available": False,
            "installed_engines": [],
            "ready_engines": [],
            "model_loading": {},
            "queue_depth": 0,
            "vram_total_mb": None,
            "vram_free_mb": None,
            "generation_available": False,
        }
    return {
        "worker_available": bool(health.get("ok")) or bool(health.get("worker_available")),
        "cuda_available": bool(health.get("cuda_available")),
        "installed_engines": list(health.get("installed_engines") or []),
        "ready_engines": list(health.get("ready_engines") or []),
        "model_loading": dict(health.get("model_loading") or {}),
        "queue_depth": int(health.get("queue_depth") or 0),
        "vram_total_mb": health.get("vram_total_mb"),
        "vram_free_mb": health.get("vram_free_mb"),
        "generation_available": bool(health.get("generation_available")),
    }

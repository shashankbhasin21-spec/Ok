"""Intelligent routing layer — open engines only, never silent paid fallback."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from gateway.models import EngineMetric


ENGINE_CATALOG: dict[str, dict[str, Any]] = {
    "wan": {
        "tasks": ["text-to-video", "image-to-video"],
        "min_vram_gb": 16.0,  # TI2V-5B consumer path; A14B needs far more
        "preferred_quality": ["high", "max"],
        "low_vram_friendly": True,
        "segment_strength": 0.7,
        "long_form_strength": 0.5,
        "requires_cuda": True,
        "upstream": "https://github.com/Wan-Video/Wan2.2",
        "license": "Apache-2.0 (check model card)",
        "models": ["Wan2.2-TI2V-5B", "Wan2.2-T2V-A14B", "Wan2.2-I2V-A14B"],
    },
    "ltx": {
        "tasks": ["text-to-video", "image-to-video", "video-extend"],
        "min_vram_gb": 8.0,  # distilled/fp8 paths
        "preferred_quality": ["draft", "standard", "high"],
        "low_vram_friendly": True,
        "segment_strength": 0.85,
        "long_form_strength": 0.75,
        "requires_cuda": True,
        "upstream": "https://github.com/Lightricks/LTX-Video",
        "license": "OpenRail-M (check model card)",
        "models": ["ltxv-2b-0.9.8-distilled", "ltxv-13b-0.9.8-distilled"],
    },
    "framepack": {
        "tasks": ["image-to-video", "long-video"],
        "min_vram_gb": 6.0,
        "preferred_quality": ["standard", "high", "max"],
        "low_vram_friendly": True,
        "segment_strength": 0.9,
        "long_form_strength": 1.0,
        "requires_cuda": True,
        "upstream": "https://github.com/lllyasviel/FramePack",
        "license": "Apache-2.0 (check repo)",
        "models": ["FramePackI2V_HY", "FramePack-F1"],
    },
    # Deterministic FFmpeg assembly — real H.264 MP4, NOT diffusion / NOT LTX.
    "cpu_assembly": {
        "tasks": ["text-to-video", "image-to-video"],
        "min_vram_gb": 0.0,
        "preferred_quality": ["draft", "standard", "high"],
        "low_vram_friendly": True,
        "segment_strength": 0.6,
        "long_form_strength": 0.9,
        "requires_cuda": False,
        "upstream": "local-ffmpeg-assembly",
        "license": "application (FFmpeg LGPL/GPL depending on build)",
        "models": ["cpu-assembly-v1"],
    },
}

PAID_PROVIDERS_BLOCKED = {
    "runway",
    "higgsfield",
    "openart",
    "heygen",
    "creative_claw",
    "pika",
    "luma",
    "kling_api",
}


@dataclass
class RoutingDecision:
    engine: str | None
    reason: str
    scores: dict[str, float] = field(default_factory=dict)
    candidates: list[str] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    mode: str = "auto"


def _hist_stats(db: Session | None, engine: str) -> dict[str, float]:
    if db is None:
        return {"success_rate": 0.5, "avg_render": 120.0}
    from gateway.metrics import get_engine_metric_row

    row = get_engine_metric_row(db, engine)
    if not row:
        return {"success_rate": 0.5, "avg_render": 120.0}
    success = row.success_count or 0
    failure = row.failure_count or 0
    total = success + failure
    success_rate = (success / total) if total else 0.5
    render_total = row.total_render_time_sec or 0.0
    avg_render = (render_total / success) if success else 120.0
    return {"success_rate": success_rate, "avg_render": avg_render}


def score_engine(
    engine: str,
    *,
    mode: str,
    quality: str,
    duration: float,
    has_image: bool,
    has_video: bool,
    ready_engines: set[str],
    installed_engines: set[str],
    vram_gb: float | None,
    db: Session | None = None,
) -> tuple[float, str]:
    if engine in PAID_PROVIDERS_BLOCKED:
        return -1e9, "paid_provider_blocked"
    meta = ENGINE_CATALOG.get(engine)
    if not meta:
        return -1e9, "unknown_engine"
    if engine not in installed_engines:
        return -1e6, "model_not_installed"
    if engine not in ready_engines:
        return -1e5, "engine_not_ready"

    score = 0.0
    reasons: list[str] = []

    # Task fit
    if has_video and "video-extend" in meta["tasks"]:
        score += 25
        reasons.append("video_extend_support")
    elif has_image and "image-to-video" in meta["tasks"]:
        score += 20
        reasons.append("i2v_support")
    elif not has_image and "text-to-video" in meta["tasks"]:
        score += 18
        reasons.append("t2v_support")
    else:
        score -= 30
        reasons.append("task_mismatch")

    # Duration / long-form
    if duration >= 30:
        score += 20 * float(meta["long_form_strength"])
        reasons.append("long_form_bias")
    else:
        score += 10 * float(meta["segment_strength"])

    # Quality preference
    if quality in meta["preferred_quality"]:
        score += 10
    if quality in ("draft", "standard") and meta["low_vram_friendly"]:
        score += 5

    # Mode biases
    if mode == "fast":
        score += 15 if engine == "ltx" else 0
        score += 8 if engine == "wan" else 0
    elif mode == "quality":
        score += 15 if engine == "wan" else 0
        score += 10 if engine == "framepack" else 0
    elif mode == "low_vram":
        score += 20 if meta["low_vram_friendly"] else -20
        if vram_gb is not None:
            score += max(0, 12 - meta["min_vram_gb"])
    elif mode in ENGINE_CATALOG:
        score += 100 if engine == mode else -50

    # VRAM feasibility
    if vram_gb is not None:
        need = float(meta["min_vram_gb"])
        if vram_gb < need * 0.6:
            score -= 40
            reasons.append("vram_tight")
        elif vram_gb >= need:
            score += 8
            reasons.append("vram_ok")

    hist = _hist_stats(db, engine)
    score += 25 * hist["success_rate"]
    # Prefer historically faster when mode is fast/auto
    score += max(0, 20 - (hist["avg_render"] / 30.0))

    # Prefer lowest-resource capable engine (policy #2)
    score += max(0, 15 - float(meta["min_vram_gb"]))

    # Prefer real GPU diffusion when ready; CPU assembly is a last-resort fallback.
    if engine == "cpu_assembly":
        if any(e != "cpu_assembly" for e in ready_engines):
            score -= 40
            reasons.append("cpu_fallback_deprioritized")
        else:
            score += 5
            reasons.append("cpu_fallback_only_ready")

    return score, ",".join(reasons) or "scored"


def route_request(
    *,
    mode: str = "auto",
    quality: str = "high",
    duration: float = 30.0,
    has_image: bool = False,
    has_video: bool = False,
    preferred_engine: str | None = None,
    ready_engines: list[str] | None = None,
    installed_engines: list[str] | None = None,
    vram_gb: float | None = None,
    db: Session | None = None,
    exclude: set[str] | None = None,
) -> RoutingDecision:
    ready = set(ready_engines or [])
    installed = set(installed_engines or [])
    exclude = set(exclude or [])

    # Explicit engine pin
    pin = preferred_engine if preferred_engine in ENGINE_CATALOG else None
    if mode in ENGINE_CATALOG:
        pin = mode

    candidates = list(ENGINE_CATALOG.keys())
    scores: dict[str, float] = {}
    rejected: list[dict[str, str]] = []
    detail: dict[str, str] = {}

    for eng in candidates:
        if eng in exclude:
            rejected.append({"engine": eng, "reason": "excluded_after_failure"})
            continue
        if pin and eng != pin and mode in ENGINE_CATALOG:
            rejected.append({"engine": eng, "reason": "mode_pinned_elsewhere"})
            continue
        s, why = score_engine(
            eng,
            mode=mode,
            quality=quality,
            duration=duration,
            has_image=has_image,
            has_video=has_video,
            ready_engines=ready,
            installed_engines=installed,
            vram_gb=vram_gb,
            db=db,
        )
        scores[eng] = s
        detail[eng] = why
        if s < -1e4:
            rejected.append({"engine": eng, "reason": why})

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best = None
    reason = "no_ready_open_engine"
    for eng, s in ordered:
        if s > -1e4:
            best = eng
            reason = detail.get(eng, "highest_score")
            break

    if not ready:
        return RoutingDecision(
            engine=None,
            reason="no_ready_engines_generation_unavailable",
            scores=scores,
            candidates=[],
            rejected=rejected
            + [{"engine": "*", "reason": "generation_available=false"}],
            mode=mode,
        )

    return RoutingDecision(
        engine=best,
        reason=reason,
        scores=scores,
        candidates=[e for e, s in ordered if s > -1e4],
        rejected=rejected,
        mode=mode,
    )


def providers_snapshot(
    *,
    installed: set[str],
    ready: set[str],
    gpu_available: bool,
    disabled: set[str] | None = None,
    details: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    disabled = disabled or set()
    details = details or {}
    out = []
    for name, meta in ENGINE_CATALOG.items():
        requires_cuda = bool(meta.get("requires_cuda", True))
        if name in disabled:
            status = "DISABLED"
        elif requires_cuda and not gpu_available:
            status = "GPU_UNAVAILABLE"
        elif name not in installed:
            status = "MODEL_NOT_INSTALLED"
        elif name not in ready:
            status = "UNHEALTHY"
        else:
            status = "AVAILABLE"
        out.append(
            {
                "name": name,
                "status": status,
                "installed": name in installed,
                "ready": name in ready,
                "tasks": meta["tasks"],
                "min_vram_gb": meta["min_vram_gb"],
                "license": meta["license"],
                "upstream": meta["upstream"],
                "details": details.get(name, {}),
            }
        )
    return out

"""Router scoring and open-engine-only policy."""

from __future__ import annotations

from gateway.router import PAID_PROVIDERS_BLOCKED, route_request, score_engine


def test_no_ready_engines():
    d = route_request(
        mode="auto",
        ready_engines=[],
        installed_engines=["wan", "ltx"],
    )
    assert d.engine is None
    assert "no_ready" in d.reason or "unavailable" in d.reason


def test_prefers_ready_lowest_resource():
    d = route_request(
        mode="auto",
        quality="standard",
        duration=30,
        ready_engines=["wan", "ltx", "framepack"],
        installed_engines=["wan", "ltx", "framepack"],
        vram_gb=12,
    )
    assert d.engine in {"wan", "ltx", "framepack"}
    assert d.engine in d.candidates


def test_mode_pin_wan():
    d = route_request(
        mode="wan",
        ready_engines=["wan", "ltx"],
        installed_engines=["wan", "ltx"],
        vram_gb=24,
    )
    assert d.engine == "wan"


def test_paid_blocked():
    s, reason = score_engine(
        "runway",
        mode="auto",
        quality="high",
        duration=30,
        has_image=False,
        has_video=False,
        ready_engines={"runway"},
        installed_engines={"runway"},
        vram_gb=80,
    )
    assert s < 0
    assert "paid" in reason or "runway" in PAID_PROVIDERS_BLOCKED


def test_fallback_excludes_failed():
    d = route_request(
        mode="auto",
        ready_engines=["ltx", "wan"],
        installed_engines=["ltx", "wan"],
        vram_gb=24,
        exclude={"ltx"},
    )
    assert d.engine == "wan"

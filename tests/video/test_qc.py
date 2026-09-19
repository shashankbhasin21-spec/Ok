"""QC validation tests using synthetic ffmpeg clips."""

from __future__ import annotations

import subprocess
from pathlib import Path

from gateway.qc import run_qc


def _make_clip(path: Path, duration: float = 2.0, size: str = "1080x1920", fps: int = 30) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={size}:rate={fps}:duration={duration}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return path


def test_qc_passes_valid_clip(tmp_path):
    clip = _make_clip(tmp_path / "ok.mp4", duration=3.0)
    result = run_qc(
        clip,
        expected_duration=3.0,
        expected_width=1080,
        expected_height=1920,
        expected_fps=30,
        min_duration=2.5,
        duration_tolerance=1.0,
    )
    assert result["checks"]["file_exists"] is True
    assert result["checks"]["decodes"] is True
    assert result["passed"] is True


def test_qc_fails_missing():
    result = run_qc(Path("/tmp/does-not-exist-video.mp4"))
    assert result["passed"] is False
    assert "file_missing" in result["failures"]


def test_qc_fails_short(tmp_path):
    clip = _make_clip(tmp_path / "short.mp4", duration=1.0)
    result = run_qc(clip, min_duration=30.0)
    assert result["passed"] is False
    assert "duration_too_short" in result["failures"]

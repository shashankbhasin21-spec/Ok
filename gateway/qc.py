"""Post-render quality control — failed QC must not be reported COMPLETED."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def _probe(path: Path) -> dict:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return {"error": proc.stderr[-500:], "streams": [], "format": {}}
    return json.loads(proc.stdout or "{}")


def _blackdetect(path: Path) -> list[dict]:
    cmd = [
        "ffmpeg",
        "-i",
        str(path),
        "-vf",
        "blackdetect=d=0.5:pix_th=0.10",
        "-an",
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    events = []
    for line in (proc.stderr or "").splitlines():
        if "black_start" in line:
            events.append({"raw": line.strip()})
    return events


def _freezedetect(path: Path) -> list[dict]:
    cmd = [
        "ffmpeg",
        "-i",
        str(path),
        "-vf",
        "freezedetect=n=0.003:d=1.0",
        "-an",
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    events = []
    for line in (proc.stderr or "").splitlines():
        if "freeze_start" in line or "freeze_duration" in line:
            events.append({"raw": line.strip()})
    return events


def run_qc(
    path: Path,
    *,
    expected_duration: float | None = None,
    expected_width: int | None = None,
    expected_height: int | None = None,
    expected_fps: float | None = None,
    require_audio: bool = False,
    min_duration: float | None = None,
    duration_tolerance: float = 1.5,
    min_file_size_bytes: int = 10_000,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "passed": False,
        "checks": {},
        "failures": [],
        "path": str(path),
    }

    if not path.exists():
        result["failures"].append("file_missing")
        result["checks"]["file_exists"] = False
        return result
    result["checks"]["file_exists"] = True

    size = path.stat().st_size
    result["checks"]["file_size"] = size
    if size < min_file_size_bytes:
        result["failures"].append("file_too_small")

    probe = _probe(path)
    if probe.get("error"):
        result["failures"].append("probe_failed")
        result["checks"]["decodes"] = False
        result["checks"]["probe_error"] = probe["error"]
        return result

    streams = probe.get("streams") or []
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    result["checks"]["decodes"] = bool(video_streams)
    if not video_streams:
        result["failures"].append("no_video_stream")
        return result

    vs = video_streams[0]
    width = int(vs.get("width") or 0)
    height = int(vs.get("height") or 0)
    result["checks"]["resolution"] = f"{width}x{height}"
    if expected_width and expected_height:
        if width != expected_width or height != expected_height:
            result["failures"].append("resolution_mismatch")

    # FPS
    fps_val = None
    rate = vs.get("avg_frame_rate") or vs.get("r_frame_rate") or "0/1"
    try:
        num, den = rate.split("/")
        fps_val = float(num) / float(den) if float(den) else None
    except Exception:
        fps_val = None
    result["checks"]["fps"] = fps_val
    if expected_fps and fps_val is not None:
        if abs(fps_val - expected_fps) > 2.0:
            result["failures"].append("fps_mismatch")

    duration = float((probe.get("format") or {}).get("duration") or vs.get("duration") or 0)
    result["checks"]["duration"] = duration
    if min_duration is not None and duration + 0.25 < min_duration:
        result["failures"].append("duration_too_short")
    if expected_duration is not None and abs(duration - expected_duration) > duration_tolerance:
        result["failures"].append("duration_mismatch")

    has_audio = bool(audio_streams)
    result["checks"]["audio_present"] = has_audio
    if require_audio and not has_audio:
        result["failures"].append("audio_missing")

    # Black / freeze heuristics (warn-heavy; fail only on extreme cases)
    black = _blackdetect(path)
    freeze = _freezedetect(path)
    result["checks"]["black_events"] = len(black)
    result["checks"]["freeze_events"] = len(freeze)
    if duration > 0 and len(black) >= 3:
        result["failures"].append("excessive_black_frames")
    if duration > 0 and len(freeze) >= 3:
        result["failures"].append("excessive_frozen_frames")

    # OpenCV optional corruption sampling
    try:
        import cv2  # type: ignore

        cap = cv2.VideoCapture(str(path))
        ok_frames = 0
        bad_frames = 0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        step = max(1, total // 20) if total else 10
        idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if idx % step == 0:
                if frame is None or frame.size == 0:
                    bad_frames += 1
                else:
                    mean = float(frame.mean())
                    if mean < 1.0 or mean > 254.0:
                        bad_frames += 1
                    else:
                        ok_frames += 1
            idx += 1
            if idx > (total or 300):
                break
        cap.release()
        result["checks"]["sampled_ok_frames"] = ok_frames
        result["checks"]["sampled_bad_frames"] = bad_frames
        if ok_frames == 0 or bad_frames > ok_frames:
            result["failures"].append("corrupted_frames")
    except Exception as exc:  # noqa: BLE001
        result["checks"]["opencv_skipped"] = str(exc)

    result["passed"] = len(result["failures"]) == 0
    return result

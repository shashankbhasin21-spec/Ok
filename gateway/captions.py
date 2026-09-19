"""Burned-in captions and optional SRT — mobile safe zones."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any


def _split_words(text: str, max_words: int) -> list[str]:
    words = re.findall(r"\S+", text.strip())
    if not words:
        return []
    chunks: list[str] = []
    for i in range(0, len(words), max_words):
        chunks.append(" ".join(words[i : i + max_words]))
    return chunks


def build_srt(
    scenes: list[dict[str, Any]],
    *,
    max_words: int = 6,
) -> str:
    lines: list[str] = []
    idx = 1
    for scene in scenes:
        text = (scene.get("caption") or scene.get("voiceover") or "").strip()
        if not text:
            continue
        start = float(scene.get("start") or 0)
        duration = float(scene.get("duration") or 3)
        chunks = _split_words(text, max_words) or [text]
        chunk_dur = duration / len(chunks)
        for i, chunk in enumerate(chunks):
            s = start + i * chunk_dur
            e = s + chunk_dur
            lines.append(str(idx))
            lines.append(f"{_ts(s)} --> {_ts(e)}")
            lines.append(chunk)
            lines.append("")
            idx += 1
    return "\n".join(lines)


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def write_srt(scenes: list[dict[str, Any]], path: Path, *, max_words: int = 6) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_srt(scenes, max_words=max_words), encoding="utf-8")
    return path


def burn_captions(
    video: Path,
    srt: Path,
    output: Path,
    *,
    font_size: int = 42,
    margin_v: int = 160,
    margin_l: int = 80,
    margin_r: int = 80,
) -> Path:
    """Burn SRT into video with bottom-safe margins for mobile UI chrome."""
    output.parent.mkdir(parents=True, exist_ok=True)
    # Escape path for ffmpeg force_style / subtitles filter.
    srt_escaped = str(srt.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    style = (
        f"FontSize={font_size},PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,"
        f"BorderStyle=3,Outline=2,Shadow=0,Alignment=2,"
        f"MarginV={margin_v},MarginL={margin_l},MarginR={margin_r}"
    )
    vf = f"subtitles='{srt_escaped}':force_style='{style}'"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video),
        "-vf",
        vf,
        "-c:a",
        "copy",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"caption burn failed: {proc.stderr[-600:]}")
    return output

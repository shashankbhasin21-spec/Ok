"""FFmpeg stitcher and social-format normalizer."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Literal

Transition = Literal["none", "crossfade", "fade"]

ASPECT_RESOLUTIONS = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}


def probe(path: Path) -> dict:
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr[-500:]}")
    return json.loads(proc.stdout or "{}")


def media_duration(path: Path) -> float:
    data = probe(path)
    fmt = data.get("format") or {}
    if "duration" in fmt:
        return float(fmt["duration"])
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video" and stream.get("duration"):
            return float(stream["duration"])
    return 0.0


def stitch_clips(
    clips: list[Path],
    output: Path,
    *,
    transition: Transition = "crossfade",
    transition_duration: float = 0.35,
    fps: int = 30,
) -> Path:
    """Concatenate clips with optional transitions. Not a slideshow renderer."""
    if not clips:
        raise ValueError("no clips to stitch")
    output.parent.mkdir(parents=True, exist_ok=True)

    if len(clips) == 1 or transition == "none":
        return _concat_demuxer(clips, output)

    if transition == "fade":
        return _fade_through_black(clips, output, transition_duration, fps)

    return _xfade_chain(clips, output, transition_duration, fps)


def _concat_demuxer(clips: list[Path], output: Path) -> Path:
    list_file = output.with_suffix(".txt")
    list_file.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        # Re-encode fallback when codecs differ.
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(output),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"stitch failed: {proc.stderr[-800:]}")
    return output


def _xfade_chain(
    clips: list[Path],
    output: Path,
    transition_duration: float,
    fps: int,
) -> Path:
    if len(clips) == 1:
        return _concat_demuxer(clips, output)

    inputs: list[str] = []
    for c in clips:
        inputs.extend(["-i", str(c)])

    # Build xfade filter graph with offset based on cumulative durations.
    filter_parts: list[str] = []
    durations = [media_duration(c) for c in clips]
    current = "[0:v]"
    offset = 0.0
    for i in range(1, len(clips)):
        offset += max(0.1, durations[i - 1] - transition_duration)
        out_label = f"[v{i}]" if i < len(clips) - 1 else "[vout]"
        filter_parts.append(
            f"{current}[{i}:v]xfade=transition=fade:duration={transition_duration}:offset={offset:.3f}{out_label}"
        )
        current = out_label

    filter_complex = ";".join(filter_parts)
    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-r",
        str(fps),
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
        # Fallback to hard cut concat if xfade fails (e.g. resolution mismatch).
        return _concat_demuxer(clips, output)
    return output


def _fade_through_black(
    clips: list[Path],
    output: Path,
    transition_duration: float,
    fps: int,
) -> Path:
    # Soft fade in/out per clip then concat — distinct from slideshow Ken Burns.
    faded: list[Path] = []
    tmp_dir = output.parent / "_fade_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for i, clip in enumerate(clips):
        out = tmp_dir / f"fade_{i:03d}.mp4"
        dur = media_duration(clip)
        fade_d = min(transition_duration, max(0.05, dur / 4))
        vf = f"fade=t=in:st=0:d={fade_d},fade=t=out:st={max(0, dur - fade_d):.3f}:d={fade_d}"
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(clip),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            faded.append(clip)
        else:
            faded.append(out)
    result = _concat_demuxer(faded, output)
    return result


def normalize_social(
    src: Path,
    dest: Path,
    *,
    aspect_ratio: str = "9:16",
    fps: int = 30,
    audio_path: Path | None = None,
) -> Path:
    """Normalize to H.264 MP4 + AAC for social platforms."""
    width, height = ASPECT_RESOLUTIONS.get(aspect_ratio, (1080, 1920))
    dest.parent.mkdir(parents=True, exist_ok=True)
    scale = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p"
    )
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if audio_path and audio_path.exists():
        cmd.extend(["-i", str(audio_path)])
        cmd.extend(
            [
                "-vf",
                scale,
                "-c:v",
                "libx264",
                "-profile:v",
                "high",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(dest),
            ]
        )
    else:
        cmd.extend(
            [
                "-vf",
                scale,
                "-c:v",
                "libx264",
                "-profile:v",
                "high",
                "-pix_fmt",
                "yuv420p",
                "-an",
                "-movflags",
                "+faststart",
                str(dest),
            ]
        )
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"normalize failed: {proc.stderr[-800:]}")
    return dest

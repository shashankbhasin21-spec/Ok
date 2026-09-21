"""Deterministic CPU video assembly — real H.264/AAC MP4 via FFmpeg.

This is NOT diffusion / LTX / Wan / FramePack generation. It assembles
prompt-conditioned stills (or provided images) with pan/zoom motion,
transitions, and standards-compliant encoding. Used only when no CUDA
diffusion engine is ready.
"""

from __future__ import annotations

import hashlib
import logging
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

from gateway.stitcher import ASPECT_RESOLUTIONS, normalize_social, stitch_clips

logger = logging.getLogger(__name__)

ENGINE_NAME = "cpu_assembly"
MODEL_NAME = "cpu-assembly-v1"

# Conservative caps to protect memory on CPU hosts.
MAX_DURATION_SEC = 60.0
MAX_WIDTH = 1080
MAX_HEIGHT = 1920
MAX_FPS = 30


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def is_ready() -> bool:
    return ffmpeg_available()


def _prompt_palette(prompt: str) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    digest = hashlib.sha256(prompt.encode("utf-8")).digest()
    # Dark premium studio palette — never bright/emoji.
    base = (12 + digest[0] % 28, 14 + digest[1] % 24, 22 + digest[2] % 40)
    accent = (40 + digest[3] % 80, 80 + digest[4] % 100, 140 + digest[5] % 90)
    highlight = (180 + digest[6] % 60, 200 + digest[7] % 40, 220 + digest[8] % 30)
    return base, accent, highlight


def _render_still(
    path: Path,
    *,
    width: int,
    height: int,
    prompt: str,
    scene_index: int,
) -> Path:
    """Create an abstract still from the prompt hash — no logos/text/people."""
    from PIL import Image, ImageDraw, ImageFilter

    path.parent.mkdir(parents=True, exist_ok=True)
    base, accent, highlight = _prompt_palette(f"{prompt}:{scene_index}")
    img = Image.new("RGB", (width, height), base)
    draw = ImageDraw.Draw(img, "RGBA")

    seed = int(hashlib.sha256(f"{prompt}:{scene_index}".encode()).hexdigest()[:8], 16)
    cx, cy = width // 2, height // 2
    # Soft radial glow core
    for i in range(8, 0, -1):
        r = int(min(width, height) * (0.08 + i * 0.06))
        alpha = 18 + i * 8
        color = (*accent, alpha)
        draw.ellipse((cx - r, cy - r - height // 10, cx + r, cy + r - height // 10), fill=color)

    # Flowing light ribbons
    for n in range(5):
        phase = ((seed >> (n * 3)) & 0xFF) / 255.0
        points = []
        for x in range(0, width, max(8, width // 40)):
            t = x / max(1, width)
            y = int(
                cy
                + math.sin((t + phase) * math.pi * 2 + scene_index) * (height * 0.12)
                + (n - 2) * (height * 0.04)
            )
            points.append((x, y))
        if len(points) >= 2:
            draw.line(points, fill=(*highlight, 90 - n * 10), width=max(2, width // 180))

    # Subtle vignette
    vignette = Image.new("RGB", (width, height), (0, 0, 0))
    mask = Image.new("L", (width, height), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((-width // 4, -height // 8, width + width // 4, height + height // 8), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(20, width // 20)))
    img = Image.composite(img, vignette, mask)
    img = img.filter(ImageFilter.GaussianBlur(radius=0.6))
    img.save(path, format="JPEG", quality=92)
    return path


def _animate_still(
    still: Path,
    output: Path,
    *,
    duration: float,
    width: int,
    height: int,
    fps: int,
    scene_index: int,
) -> Path:
    """Pan/zoom motion via FFmpeg zoompan — real encoded video frames."""
    output.parent.mkdir(parents=True, exist_ok=True)
    # Oversample still then zoompan for smoother motion.
    zw, zh = width * 2, height * 2
    frames = max(1, int(duration * fps))
    # Alternate zoom-in / drift patterns per scene.
    if scene_index % 2 == 0:
        zexpr = f"min(zoom+0.0008,1.18)"
        xexpr = "iw/2-(iw/zoom/2)"
        yexpr = "ih/2-(ih/zoom/2)"
    else:
        zexpr = f"if(eq(on,1),1.12,max(zoom-0.0006,1.0))"
        xexpr = f"iw/2-(iw/zoom/2)+{(scene_index % 3) - 1}*on*0.15"
        yexpr = f"ih/2-(ih/zoom/2)"

    vf = (
        f"scale={zw}:{zh},"
        f"zoompan=z='{zexpr}':x='{xexpr}':y='{yexpr}':d={frames}:s={width}x{height}:fps={fps},"
        f"format=yuv420p"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(still),
        "-vf",
        vf,
        "-t",
        f"{duration:.3f}",
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-an",
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not output.exists():
        raise RuntimeError(
            f"cpu_assembly encode failed: {(proc.stderr or '')[-400:] or proc.returncode}"
        )
    return output


def _bounded_resolution(aspect_ratio: str, resolution: str | None) -> tuple[int, int]:
    if resolution:
        try:
            w_s, h_s = resolution.lower().split("x")
            w, h = int(w_s), int(h_s)
            # Keep aspect, clamp size.
            scale = min(1.0, MAX_WIDTH / max(1, w), MAX_HEIGHT / max(1, h))
            w = max(64, int(w * scale) // 2 * 2)
            h = max(64, int(h * scale) // 2 * 2)
            return w, h
        except Exception:
            pass
    w, h = ASPECT_RESOLUTIONS.get(aspect_ratio, (1080, 1920))
    # Conservative default for CPU hosts.
    if aspect_ratio == "9:16":
        return 720, 1280
    if aspect_ratio == "16:9":
        return 1280, 720
    if aspect_ratio == "1:1":
        return 720, 720
    return min(w, 1080), min(h, 1920)


def generate_scene_clip(
    *,
    prompt: str,
    duration: float,
    aspect_ratio: str,
    resolution: str | None,
    fps: int,
    scene_index: int,
    output_path: Path,
    input_image: str | None = None,
) -> dict[str, Any]:
    if not is_ready():
        return {
            "ok": False,
            "error": "ffmpeg/ffprobe unavailable",
            "error_category": "dependency_missing",
            "engine": ENGINE_NAME,
        }
    duration = max(0.5, min(float(duration), MAX_DURATION_SEC))
    fps = max(8, min(int(fps), MAX_FPS))
    width, height = _bounded_resolution(aspect_ratio, resolution)
    temp_dir = output_path.parent
    still = temp_dir / f"cpu_still_{scene_index:03d}.jpg"

    try:
        if input_image and Path(input_image).exists():
            from PIL import Image

            img = Image.open(input_image).convert("RGB")
            img = img.resize((width, height))
            img.save(still, format="JPEG", quality=92)
        else:
            _render_still(
                still,
                width=width,
                height=height,
                prompt=prompt,
                scene_index=scene_index,
            )
        _animate_still(
            still,
            output_path,
            duration=duration,
            width=width,
            height=height,
            fps=fps,
            scene_index=scene_index,
        )
        return {
            "ok": True,
            "output_path": str(output_path),
            "engine": ENGINE_NAME,
            "model": MODEL_NAME,
            "meta": {
                "backend": "ffmpeg_cpu_assembly",
                "not_diffusion": True,
                "width": width,
                "height": height,
            },
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("cpu_assembly scene %s failed: %s", scene_index, type(exc).__name__)
        return {
            "ok": False,
            "error": str(exc)[:500],
            "error_category": "generation_failed",
            "engine": ENGINE_NAME,
        }


def assemble_job_clips(
    scenes: list[dict[str, Any]],
    *,
    prompt: str,
    aspect_ratio: str,
    resolution: str | None,
    fps: int,
    temp_dir: Path,
    input_image: str | None = None,
    transition: str = "crossfade",
) -> list[Path]:
    """Render each scene via CPU assembly and return clip paths."""
    clips: list[Path] = []
    for scene in scenes:
        idx = int(scene["index"])
        out = temp_dir / f"scene_{idx:03d}.mp4"
        result = generate_scene_clip(
            prompt=scene.get("visual_prompt") or prompt,
            duration=float(scene.get("duration") or 3.0),
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            fps=fps,
            scene_index=idx,
            output_path=out,
            input_image=input_image if idx == 0 else None,
        )
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "cpu_assembly failed")
        clips.append(Path(result["output_path"]))
    return clips

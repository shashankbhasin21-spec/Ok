"""Video and image rendering — real files on disk, no stock-footage fakery.

Claude writes the script; this renders it. Frames are drawn with Pillow and
assembled into an MP4 with ffmpeg when it's installed (Instagram Reels needs
MP4/H.264). Without ffmpeg you still get every frame plus an animated GIF, so
the pipeline never silently produces nothing.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

REEL_SIZE = (1080, 1920)  # 9:16, the format Reels actually wants
POST_SIZE = (1080, 1350)  # 4:5 feed post

PALETTES = {
    "ink": ((14, 17, 22), (245, 243, 238), (233, 116, 81)),
    "sand": ((243, 238, 228), (28, 26, 24), (176, 96, 62)),
    "deep": ((17, 24, 39), (248, 250, 252), (99, 179, 237)),
}


@dataclass
class Scene:
    text: str
    seconds: float = 2.5
    emphasis: bool = False


@dataclass
class VideoSpec:
    title: str
    scenes: list[Scene] = field(default_factory=list)
    palette: str = "ink"
    fps: int = 30


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _font(size: int):
    from PIL import ImageFont

    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size)


def render_frame(text: str, size: tuple[int, int], palette: str, *, emphasis: bool = False):
    from PIL import Image, ImageDraw

    bg, fg, accent = PALETTES.get(palette, PALETTES["ink"])
    img = Image.new("RGB", size, bg)
    draw = ImageDraw.Draw(img)

    width, height = size
    safe = width * 0.86  # keep text clear of the edges on a phone screen

    # Shrink until the longest wrapped line actually measures inside the frame.
    # Estimating from character counts overflows on wide glyphs.
    font_size = int(width * (0.095 if emphasis else 0.072))
    while font_size > 18:
        font = _font(font_size)
        # Wrap width from the text's own average glyph width, not a guess.
        measured = draw.textlength(text, font=font)
        chars = max(8, int(len(text) * safe / measured)) if measured else len(text)
        wrapped = textwrap.wrap(text, width=chars) or [text]
        if max(draw.textlength(line, font=font) for line in wrapped) <= safe:
            break
        font_size = int(font_size * 0.92)

    line_height = int(font_size * 1.3)
    total = line_height * len(wrapped)
    y = (height - total) // 2

    # Accent rule above the text block — gives every frame a consistent anchor.
    bar = int(width * 0.16)
    bar_x = (width - bar) // 2
    draw.rectangle(
        [(bar_x, y - int(font_size * 0.9)), (bar_x + bar, y - int(font_size * 0.78))],
        fill=accent,
    )

    for line in wrapped:
        box = draw.textbbox((0, 0), line, font=font)
        draw.text(((width - (box[2] - box[0])) // 2, y), line, font=font,
                  fill=accent if emphasis else fg)
        y += line_height
    return img


def render_video(spec: VideoSpec, outdir: Path, size: tuple[int, int] = REEL_SIZE) -> dict:
    """Render the spec. Returns paths to whatever was produced."""
    outdir = Path(outdir)
    frames_dir = outdir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    images, index = [], 0
    for scene in spec.scenes:
        frame = render_frame(scene.text, size, spec.palette, emphasis=scene.emphasis)
        for _ in range(max(1, int(scene.seconds * spec.fps))):
            frame.save(frames_dir / f"{index:05d}.png")
            index += 1
        images.append(frame)

    result: dict = {"frames": index, "frames_dir": str(frames_dir)}
    if images:
        gif = outdir / "preview.gif"
        images[0].save(
            gif, save_all=True, append_images=images[1:],
            duration=int(max(s.seconds for s in spec.scenes) * 1000), loop=0,
        )
        result["gif"] = str(gif)
        poster = outdir / "poster.png"
        images[0].save(poster)
        result["poster"] = str(poster)

    if has_ffmpeg() and index:
        mp4 = outdir / "reel.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-framerate", str(spec.fps), "-i", str(frames_dir / "%05d.png"),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)],
            check=True, capture_output=True,
        )
        result["mp4"] = str(mp4)
    else:
        result["note"] = (
            "ffmpeg not installed — frames and GIF rendered, but Instagram Reels needs MP4. "
            "Install ffmpeg and re-run to get reel.mp4."
        )
    return result

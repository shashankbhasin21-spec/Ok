"""Continuity metadata and conditioning helpers between scenes."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def build_continuity_metadata(
    *,
    style_lock: str,
    character_lock: str,
    environment_lock: str,
    base_seed: int | None,
    scene_count: int,
) -> dict[str, Any]:
    return {
        "style_lock": style_lock,
        "character_lock": character_lock,
        "environment_lock": environment_lock,
        "seed_strategy": "base_seed_plus_scene_index",
        "base_seed": base_seed,
        "scene_count": scene_count,
        "techniques": [
            "repeated_character_descriptions",
            "style_locking",
            "environment_locking",
            "camera_language_consistency",
            "final_frame_to_next_i2v_when_supported",
            "consistent_seed_strategy",
        ],
        "frame_links": [],
    }


def extract_last_frame(video_path: Path, output_image: Path) -> Path | None:
    """Extract the final frame of a clip for next-scene I2V conditioning."""
    output_image.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-sseof",
        "-0.1",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(output_image),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0 and output_image.exists():
            return output_image
    except FileNotFoundError:
        return None
    return None


def link_scene_frame(
    continuity: dict[str, Any],
    *,
    from_scene: int,
    to_scene: int,
    frame_path: str,
) -> dict[str, Any]:
    continuity = dict(continuity)
    links = list(continuity.get("frame_links") or [])
    links.append(
        {
            "from_scene": from_scene,
            "to_scene": to_scene,
            "frame_path": frame_path,
            "method": "final_frame_extraction",
        }
    )
    continuity["frame_links"] = links
    return continuity

"""Deterministic scene planner — no external LLM required."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from gateway.schemas import ScenePlan, SceneSpec


CAMERA_CYCLES = [
    "smooth tracking shot, slight forward dolly",
    "gentle orbit, cinematic framing",
    "slow push-in, shallow depth of field",
    "locked wide establishing shot with subtle parallax",
    "handheld-feel micro movement, stable horizon",
]

MOTION_CYCLES = [
    "natural continuous motion, coherent physics",
    "purposeful subject movement through the environment",
    "atmospheric environmental motion, drifting light",
    "dynamic but controlled action beats",
    "slow cinematic pacing with clear subject focus",
]


def _seed_from(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)


def _split_beats(prompt: str, n: int) -> list[str]:
    """Split prompt into coherent beat phrases without an LLM."""
    parts = [p.strip() for p in re.split(r"[.;\n]+", prompt) if p.strip()]
    if not parts:
        parts = [prompt.strip()]
    if len(parts) >= n:
        return parts[:n]
    # Expand by appending progressive narrative beats derived from the prompt.
    base = parts[0]
    expansions = [
        f"{base}, establishing wide view",
        f"{base}, medium shot focusing on the main subject",
        f"{base}, detail close-up with cinematic lighting",
        f"{base}, continuing motion through the same environment",
        f"{base}, climax moment with stronger motion",
        f"{base}, resolving wide shot, premium commercial finish",
    ]
    out = list(parts)
    i = 0
    while len(out) < n:
        out.append(expansions[i % len(expansions)])
        i += 1
    return out[:n]


def plan_scene_durations(total: float, preferred_segment: float = 6.0) -> list[float]:
    """Split total duration into scene segments without forcing one diffusion pass."""
    if total <= preferred_segment:
        return [round(total, 3)]
    n = max(2, int(math.ceil(total / preferred_segment)))
    base = total / n
    # Vary slightly for natural pacing while summing exactly to total.
    durations: list[float] = []
    remaining = total
    for i in range(n):
        if i == n - 1:
            durations.append(round(remaining, 3))
            break
        # Alternate slightly longer/shorter beats.
        factor = 1.1 if i % 2 == 0 else 0.9
        d = max(2.0, min(preferred_segment * 1.4, base * factor))
        d = round(d, 3)
        durations.append(d)
        remaining = round(remaining - d, 3)
        if remaining <= 0:
            durations[-1] = round(durations[-1] + remaining, 3)
            remaining = 0
            break
    # Normalize tiny float drift.
    drift = round(total - sum(durations), 3)
    if durations and abs(drift) >= 0.001:
        durations[-1] = round(durations[-1] + drift, 3)
    return durations


def build_scene_plan(
    prompt: str,
    *,
    duration: float = 30.0,
    aspect_ratio: str = "9:16",
    negative_prompt: str = "",
    seed: int | None = None,
    style_lock: str | None = None,
    character_lock: str | None = None,
    environment_lock: str | None = None,
    title: str | None = None,
) -> ScenePlan:
    durations = plan_scene_durations(duration)
    beats = _split_beats(prompt, len(durations))
    base_seed = seed if seed is not None else _seed_from(prompt)
    style = style_lock or "cinematic lighting, premium commercial aesthetic, coherent color grade"
    character = character_lock or ""
    environment = environment_lock or ""

    scenes: list[SceneSpec] = []
    t = 0.0
    for i, (seg_dur, beat) in enumerate(zip(durations, beats)):
        camera = CAMERA_CYCLES[i % len(CAMERA_CYCLES)]
        motion = MOTION_CYCLES[i % len(MOTION_CYCLES)]
        continuity_bits = [
            "same subject identity",
            "same environment and lighting continuity",
            f"style lock: {style}",
        ]
        if character:
            continuity_bits.append(f"character lock: {character}")
        if environment:
            continuity_bits.append(f"environment lock: {environment}")
        if i > 0:
            continuity_bits.append("continue from previous final frame when I2V supported")

        visual = ", ".join(
            x
            for x in [
                beat,
                character,
                environment,
                style,
                camera,
                motion,
                f"scene {i + 1} of {len(durations)}",
            ]
            if x
        )
        caption = beat if len(beat) <= 80 else beat[:77] + "..."
        scenes.append(
            SceneSpec(
                index=i,
                start=round(t, 3),
                duration=seg_dur,
                visual_prompt=visual,
                camera=camera,
                motion=motion,
                continuity="; ".join(continuity_bits),
                voiceover=beat,
                caption=caption,
                negative_prompt=negative_prompt
                or "blurry, watermark, text overlay, morphing face, flicker, low quality",
                seed=base_seed + i,
            )
        )
        t += seg_dur

    return ScenePlan(
        title=title or (prompt[:60] + ("…" if len(prompt) > 60 else "")),
        duration=duration,
        aspect_ratio=aspect_ratio,
        scenes=scenes,
        style_lock=style,
        character_lock=character,
        environment_lock=environment,
    )


def scene_plan_to_dict(plan: ScenePlan) -> dict[str, Any]:
    return plan.model_dump()

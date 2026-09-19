"""Deterministic scene planner — no LLM required."""

from __future__ import annotations

from gateway.scene_planner import build_scene_plan, plan_scene_durations


def test_plan_durations_sum_to_total():
    durs = plan_scene_durations(30.0, preferred_segment=6.0)
    assert abs(sum(durs) - 30.0) < 0.01
    assert len(durs) >= 4


def test_scene_plan_structure():
    plan = build_scene_plan(
        "An advanced humanoid AI robot walking through a futuristic office",
        duration=30,
        aspect_ratio="9:16",
        seed=42,
        character_lock="humanoid AI robot",
        environment_lock="futuristic office",
    )
    assert plan.duration == 30
    assert plan.aspect_ratio == "9:16"
    assert len(plan.scenes) >= 4
    assert abs(sum(s.duration for s in plan.scenes) - 30) < 0.05
    assert all(s.visual_prompt for s in plan.scenes)
    assert "humanoid" in plan.scenes[0].visual_prompt.lower() or "robot" in plan.scenes[0].visual_prompt.lower()
    # Deterministic seeds
    plan2 = build_scene_plan(
        "An advanced humanoid AI robot walking through a futuristic office",
        duration=30,
        seed=42,
        character_lock="humanoid AI robot",
        environment_lock="futuristic office",
    )
    assert [s.seed for s in plan.scenes] == [s.seed for s in plan2.scenes]


def test_short_duration_single_scene():
    plan = build_scene_plan("quick shot", duration=4, aspect_ratio="9:16")
    assert len(plan.scenes) == 1

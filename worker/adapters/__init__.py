"""Adapter package."""

from worker.adapters.base import EngineAdapter, GenerateRequest, GenerateResult
from worker.adapters.framepack import FramePackAdapter
from worker.adapters.ltx import LTXAdapter
from worker.adapters.wan import WanAdapter

__all__ = [
    "EngineAdapter",
    "GenerateRequest",
    "GenerateResult",
    "WanAdapter",
    "LTXAdapter",
    "FramePackAdapter",
]

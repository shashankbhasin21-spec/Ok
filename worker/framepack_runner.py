"""FramePack inference runner — imports upstream modules when FRAMEPACK_REPO is set.

This script is invoked by FramePackAdapter. It does NOT synthesize fake video.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--model-cache", default="./models")
    parser.add_argument("--low-vram", action="store_true")
    args = parser.parse_args()

    repo = Path(args.repo)
    if not repo.exists():
        print(f"FramePack repo missing: {repo}", file=sys.stderr)
        return 2
    sys.path.insert(0, str(repo))

    try:
        # Upstream FramePack exposes sampling through its demo modules.
        # Import paths follow the official repository layout.
        import torch
        from diffusers import AutoencoderKLHunyuanVideo  # type: ignore
        from diffusers_helper.models.hunyuan_video_packed import (  # type: ignore
            HunyuanVideoTransformer3DModelPacked,
        )
        from diffusers_helper.pipelines.k_diffusion_hunyuan import sample_hunyuan  # type: ignore
        from diffusers_helper.utils import save_bcthw_as_mp4  # type: ignore
        from PIL import Image
        import numpy as np
        import einops
    except Exception as exc:  # noqa: BLE001
        print(
            "FramePack upstream imports failed. Clone https://github.com/lllyasviel/FramePack "
            f"and install its requirements. Error: {exc}",
            file=sys.stderr,
        )
        return 3

    if not torch.cuda.is_available():
        print("CUDA required for FramePack", file=sys.stderr)
        return 4

    # High-level progressive sampling matching FramePack's design.
    # Exact transformer loading mirrors demo_gradio defaults when available.
    try:
        from diffusers_helper.hunyuan import encode_prompt_conds, vae_decode  # type: ignore
        from diffusers_helper.utils import crop_or_pad_yield_mask  # type: ignore
    except Exception:
        pass

    print(
        "FramePack runner: loading models from cache/HF (this requires installed weights). "
        "If weights are missing, download via scripts/setup_models.py --engine framepack"
    )

    # Prefer calling into demo_gradio worker function if exported.
    demo = repo / "demo_gradio.py"
    if demo.exists():
        # Dynamic import of worker function is fragile across versions —
        # fall through to explicit failure rather than fake output.
        print(
            "FramePack desktop demo present. Full headless sampling requires matching "
            "installed transformer weights (FramePackI2V_HY). "
            "Weights not auto-invoked here to avoid incompatible API assumptions.",
            file=sys.stderr,
        )

    # Attempt a minimal packed transformer load from HF cache.
    model_id = "lllyasviel/FramePackI2V_HY"
    try:
        transformer = HunyuanVideoTransformer3DModelPacked.from_pretrained(
            model_id,
            torch_dtype=torch.bfloat16,
            cache_dir=str(Path(args.model_cache) / "hf"),
        )
        transformer.eval()
        if args.low_vram:
            transformer.enable_sequential_cpu_offload()  # type: ignore[attr-defined]
        else:
            transformer.to("cuda")
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to load FramePack transformer: {exc}", file=sys.stderr)
        return 5

    image = Image.open(args.image).convert("RGB")
    # Without full demo pipeline wiring (text encoder + VAE + sampling loop),
    # we refuse to invent frames. Signal clear failure category.
    _ = (image, sample_hunyuan, save_bcthw_as_mp4, einops, np, AutoencoderKLHunyuanVideo, args)
    print(
        "FramePack transformer loaded, but complete headless pipeline wiring depends on "
        "matching upstream demo version. Install weights and use the official demo_gradio "
        "path, or extend framepack_runner.py against your pinned FramePack commit.",
        file=sys.stderr,
    )
    return 6


if __name__ == "__main__":
    raise SystemExit(main())

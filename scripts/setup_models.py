#!/usr/bin/env python3
"""Download / verify open video model weights into MODEL_CACHE. Never commits weights."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description="Setup open video model weights")
    p.add_argument("--engine", choices=["wan", "ltx", "framepack", "all"], default="all")
    p.add_argument("--model-cache", default=os.environ.get("MODEL_CACHE", "./models"))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    cache = Path(args.model_cache)
    cache.mkdir(parents=True, exist_ok=True)

    targets = {
        "wan": [
            ("Wan-AI/Wan2.2-TI2V-5B", cache / "wan" / "Wan2.2-TI2V-5B"),
        ],
        "ltx": [
            ("Lightricks/LTX-Video", cache / "ltx" / "LTX-Video"),
        ],
        "framepack": [
            ("lllyasviel/FramePackI2V_HY", cache / "framepack" / "FramePackI2V_HY"),
        ],
    }
    engines = list(targets) if args.engine == "all" else [args.engine]

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("Install huggingface_hub: pip install huggingface_hub", file=sys.stderr)
        return 1

    for eng in engines:
        for repo_id, local_dir in targets[eng]:
            print(f"[{eng}] {repo_id} -> {local_dir}")
            if args.dry_run:
                continue
            local_dir.mkdir(parents=True, exist_ok=True)
            snapshot_download(
                repo_id=repo_id,
                local_dir=str(local_dir),
                token=os.environ.get("HF_TOKEN") or None,
            )
            print(f"  done: {local_dir}")
    print("NOTE: OPEN-SOURCE MODEL != FREE COMPUTE. GPU time still costs money/electricity.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

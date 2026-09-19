"""CUDA / VRAM detection — never invent GPU availability."""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


def detect_cuda() -> dict[str, Any]:
    info: dict[str, Any] = {
        "cuda_available": False,
        "device_count": 0,
        "devices": [],
        "vram_total_mb": None,
        "vram_free_mb": None,
        "cuda_version": None,
        "torch_version": None,
        "source": None,
    }

    # Prefer torch if installed.
    try:
        import torch

        info["torch_version"] = torch.__version__
        if torch.cuda.is_available():
            info["cuda_available"] = True
            info["device_count"] = torch.cuda.device_count()
            info["cuda_version"] = torch.version.cuda
            info["source"] = "torch"
            total = 0
            free = 0
            for i in range(info["device_count"]):
                props = torch.cuda.get_device_properties(i)
                free_b, total_b = torch.cuda.mem_get_info(i)
                total += total_b
                free += free_b
                info["devices"].append(
                    {
                        "index": i,
                        "name": props.name,
                        "total_mb": round(total_b / (1024 * 1024), 1),
                        "free_mb": round(free_b / (1024 * 1024), 1),
                    }
                )
            info["vram_total_mb"] = round(total / (1024 * 1024), 1)
            info["vram_free_mb"] = round(free / (1024 * 1024), 1)
            return info
    except Exception as exc:  # noqa: BLE001
        logger.debug("torch cuda probe failed: %s", exc)

    # Fallback: nvidia-smi
    if shutil.which("nvidia-smi"):
        try:
            proc = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,memory.free,driver_version",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                info["cuda_available"] = True
                info["source"] = "nvidia-smi"
                total = 0.0
                free = 0.0
                for i, line in enumerate(proc.stdout.strip().splitlines()):
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) < 3:
                        continue
                    name, tot, fre = parts[0], float(parts[1]), float(parts[2])
                    total += tot
                    free += fre
                    info["devices"].append(
                        {
                            "index": i,
                            "name": name,
                            "total_mb": tot,
                            "free_mb": fre,
                        }
                    )
                info["device_count"] = len(info["devices"])
                info["vram_total_mb"] = total
                info["vram_free_mb"] = free
                return info
        except Exception as exc:  # noqa: BLE001
            logger.debug("nvidia-smi probe failed: %s", exc)

    return info


def peak_vram_mb() -> float | None:
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        return round(torch.cuda.max_memory_allocated() / (1024 * 1024), 1)
    except Exception:
        return None

"""FramePack adapter — particularly useful for longer video generation.

Official: https://github.com/lllyasviel/FramePack
Next-frame-section prediction; works from ~6GB VRAM with 13B models.
Primarily image-to-video / long-form progressive generation.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from worker.adapters.base import EngineAdapter, GenerateRequest, GenerateResult
from worker.gpu import peak_vram_mb

logger = logging.getLogger(__name__)

FRAMEPACK_MODELS = {
    "FramePackI2V_HY": {
        "hf": "lllyasviel/FramePackI2V_HY",
        "min_vram_gb": 6.0,
    },
    "FramePack_F1_HY": {
        "hf": "lllyasviel/FramePack_F1_I2V_HY_20250503",
        "min_vram_gb": 6.0,
    },
}


class FramePackAdapter(EngineAdapter):
    name = "framepack"
    upstream = "https://github.com/lllyasviel/FramePack"
    license_note = "Apache-2.0 (verify repo LICENSE and model cards)"
    supported_tasks = ["image-to-video", "long-video"]
    min_vram_gb = 6.0

    def _weights_dir(self) -> Path:
        return self.model_cache / "framepack"

    def _detect_installed(self) -> list[str]:
        found = []
        root = self._weights_dir()
        if root.exists():
            for name in FRAMEPACK_MODELS:
                if (root / name).exists() or any(root.rglob(f"*{name}*")):
                    found.append(name)
        hf = self.model_cache / "hf"
        if hf.exists():
            for name, meta in FRAMEPACK_MODELS.items():
                slug = meta["hf"].replace("/", "--")
                if list(hf.glob(f"models--{slug}*")):
                    found.append(name)
        return sorted(set(found))

    def is_installed(self) -> bool:
        return bool(self._detect_installed()) or (
            self.repo_path is not None and (self.repo_path / "demo_gradio.py").exists()
        )

    def is_ready(self, *, cuda_available: bool, vram_gb: float | None) -> bool:
        if not cuda_available:
            return False
        # FramePack needs an image for I2V; still "ready" if weights present —
        # gateway can supply continuity frames.
        if not self._detect_installed() and not (
            self.repo_path and (self.repo_path / "demo_gradio.py").exists()
        ):
            return False
        if vram_gb is not None and vram_gb < 5.5:
            return False
        return bool(self._detect_installed())

    def list_models(self) -> list[dict[str, Any]]:
        installed = set(self._detect_installed())
        return [
            {
                "name": name,
                "installed": name in installed,
                "hf": meta["hf"],
                "min_vram_gb": meta["min_vram_gb"],
            }
            for name, meta in FRAMEPACK_MODELS.items()
        ]

    def generate(self, req: GenerateRequest, cancel_flag: Callable[[], bool]) -> GenerateResult:
        if cancel_flag():
            return GenerateResult(ok=False, error="cancelled", error_category="cancelled", engine=self.name)

        installed = self._detect_installed()
        if not installed and not (self.repo_path and (self.repo_path / "demo_gradio.py").exists()):
            return GenerateResult(
                ok=False,
                error="FramePack weights not installed",
                error_category="model_not_installed",
                engine=self.name,
            )

        if not req.input_image:
            return GenerateResult(
                ok=False,
                error="FramePack requires an input image (I2V / continuity frame)",
                error_category="task_incompatible",
                engine=self.name,
            )

        model = installed[0] if installed else "FramePackI2V_HY"
        return self._generate_worker_script(req, model, cancel_flag)

    def _generate_worker_script(
        self,
        req: GenerateRequest,
        model: str,
        cancel_flag: Callable[[], bool],
    ) -> GenerateResult:
        """Invoke FramePack sampling via a small runner that imports upstream modules."""
        out = Path(req.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        if not self.repo_path or not self.repo_path.exists():
            return GenerateResult(
                ok=False,
                error="FramePack repo not cloned — set FRAMEPACK_REPO and install weights",
                error_category="dependency_missing",
                engine=self.name,
                model=model,
            )

        runner = self.repo_path / "tools" if False else None  # placate linters
        # Prefer a local helper script we ship that imports FramePack APIs.
        helper = Path(__file__).resolve().parents[1] / "framepack_runner.py"
        cmd = [
            sys.executable,
            str(helper),
            "--repo",
            str(self.repo_path),
            "--image",
            req.input_image,
            "--prompt",
            req.prompt,
            "--output",
            str(out),
            "--seconds",
            str(req.duration),
            "--model-cache",
            str(self.model_cache),
        ]
        if req.seed is not None:
            cmd.extend(["--seed", str(req.seed)])
        if req.low_vram:
            cmd.append("--low-vram")

        self._loading = True
        self._loaded_model = model
        t0 = time.time()
        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(self.repo_path) + os.pathsep + env.get("PYTHONPATH", "")
            env["HF_HOME"] = str(self.model_cache / "hf")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            assert proc.stdout is not None
            buf: list[str] = []
            for line in proc.stdout:
                buf.append(line)
                if cancel_flag():
                    proc.terminate()
                    return GenerateResult(
                        ok=False, error="cancelled", error_category="cancelled", engine=self.name
                    )
            rc = proc.wait()
            if rc != 0 or not out.exists():
                text = "".join(buf)
                return GenerateResult(
                    ok=False,
                    error=text[-800:] or f"framepack exit {rc}",
                    error_category="oom" if "out of memory" in text.lower() else "inference_failed",
                    engine=self.name,
                    model=model,
                )
            return GenerateResult(
                ok=True,
                output_path=str(out),
                model=model,
                engine=self.name,
                peak_vram_mb=peak_vram_mb(),
                render_time_sec=time.time() - t0,
                meta={"backend": "framepack_runner"},
            )
        except Exception as exc:  # noqa: BLE001
            return GenerateResult(
                ok=False,
                error=str(exc),
                error_category="inference_failed",
                engine=self.name,
                model=model,
            )
        finally:
            self._loading = False
            _ = runner

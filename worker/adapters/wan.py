"""Wan 2.2 adapter — genuine upstream inference via generate.py / Diffusers.

Official: https://github.com/Wan-Video/Wan2.2
Does not invent unsupported flags. Uses upstream CLI when repo+weights exist.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from worker.adapters.base import EngineAdapter, GenerateRequest, GenerateResult
from worker.gpu import peak_vram_mb

logger = logging.getLogger(__name__)

# Prefer lower-resource TI2V-5B for consumer GPUs; A14B needs ~80GB.
WAN_MODELS = {
    "Wan2.2-TI2V-5B": {
        "hf": "Wan-AI/Wan2.2-TI2V-5B",
        "task": "ti2v-5B",
        "min_vram_gb": 16.0,
        "size_default": "1280*704",
    },
    "Wan2.2-T2V-A14B": {
        "hf": "Wan-AI/Wan2.2-T2V-A14B",
        "task": "t2v-A14B",
        "min_vram_gb": 80.0,
        "size_default": "1280*720",
    },
    "Wan2.2-I2V-A14B": {
        "hf": "Wan-AI/Wan2.2-I2V-A14B",
        "task": "i2v-A14B",
        "min_vram_gb": 80.0,
        "size_default": "1280*720",
    },
}


class WanAdapter(EngineAdapter):
    name = "wan"
    upstream = "https://github.com/Wan-Video/Wan2.2"
    license_note = "Apache-2.0 code; check HuggingFace model cards for weights"
    supported_tasks = ["text-to-video", "image-to-video"]
    min_vram_gb = 16.0

    def _ckpt_dir(self, model_name: str) -> Path:
        return self.model_cache / "wan" / model_name

    def _detect_installed_models(self) -> list[str]:
        found = []
        for name in WAN_MODELS:
            d = self._ckpt_dir(name)
            if d.exists() and any(d.iterdir()):
                found.append(name)
            # Also accept HF cache snapshots
            hf_dir = self.model_cache / "hf" / f"models--Wan-AI--{name}"
            if hf_dir.exists():
                found.append(name)
        return sorted(set(found))

    def is_installed(self) -> bool:
        return bool(self._detect_installed_models()) or self._repo_ready()

    def _repo_ready(self) -> bool:
        if not self.repo_path:
            return False
        return (self.repo_path / "generate.py").exists()

    def is_ready(self, *, cuda_available: bool, vram_gb: float | None) -> bool:
        if not cuda_available:
            return False
        if not self._detect_installed_models():
            return False
        if vram_gb is not None and vram_gb < 10:
            return False
        return True

    def list_models(self) -> list[dict[str, Any]]:
        installed = set(self._detect_installed_models())
        out = []
        for name, meta in WAN_MODELS.items():
            out.append(
                {
                    "name": name,
                    "installed": name in installed,
                    "hf": meta["hf"],
                    "task": meta["task"],
                    "min_vram_gb": meta["min_vram_gb"],
                    "path": str(self._ckpt_dir(name)),
                }
            )
        return out

    def _select_model(self, req: GenerateRequest, vram_gb: float | None) -> str | None:
        installed = self._detect_installed_models()
        if not installed:
            return None
        if req.task == "image-to-video":
            for preferred in ("Wan2.2-TI2V-5B", "Wan2.2-I2V-A14B"):
                if preferred in installed:
                    if preferred.endswith("A14B") and vram_gb and vram_gb < 60:
                        continue
                    return preferred
        for preferred in ("Wan2.2-TI2V-5B", "Wan2.2-T2V-A14B"):
            if preferred in installed:
                if preferred.endswith("A14B") and vram_gb and vram_gb < 60:
                    continue
                return preferred
        return installed[0]

    def generate(self, req: GenerateRequest, cancel_flag: Callable[[], bool]) -> GenerateResult:
        if cancel_flag():
            return GenerateResult(ok=False, error="cancelled", error_category="cancelled", engine=self.name)

        model = self._select_model(req, None)
        if not model:
            return GenerateResult(
                ok=False,
                error="Wan model weights not installed",
                error_category="model_not_installed",
                engine=self.name,
            )
        if not self._repo_ready():
            # Attempt Diffusers path when official repo not cloned.
            return self._generate_diffusers(req, model, cancel_flag)

        return self._generate_cli(req, model, cancel_flag)

    def _size_arg(self, req: GenerateRequest, model: str) -> str:
        w, h = self.parse_resolution(req.resolution, req.aspect_ratio)
        # Wan expects width*height; TI2V docs use 1280*704 for 16:9-ish.
        # For vertical social, request matching orientation at supported sizes.
        if req.aspect_ratio == "9:16":
            return "704*1280" if "5B" in model else "720*1280"
        if req.aspect_ratio == "1:1":
            return "720*720"
        return WAN_MODELS[model]["size_default"]

    def _generate_cli(
        self,
        req: GenerateRequest,
        model: str,
        cancel_flag: Callable[[], bool],
    ) -> GenerateResult:
        assert self.repo_path is not None
        ckpt = self._ckpt_dir(model)
        if not ckpt.exists() or not any(ckpt.iterdir()):
            return GenerateResult(
                ok=False,
                error=f"checkpoint missing: {ckpt}",
                error_category="model_not_installed",
                engine=self.name,
            )

        out = Path(req.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        meta = WAN_MODELS[model]
        task = meta["task"]
        if req.input_image and "ti2v" in task:
            task = "ti2v-5B"
        elif req.input_image:
            task = "i2v-A14B" if "I2V" in model or "A14B" in model else task

        cmd = [
            sys.executable,
            str(self.repo_path / "generate.py"),
            "--task",
            task,
            "--size",
            self._size_arg(req, model),
            "--ckpt_dir",
            str(ckpt),
            "--prompt",
            req.prompt,
            "--save_file",
            str(out),
        ]
        # Upstream-supported low-VRAM options only.
        if req.low_vram or req.quality in ("draft", "standard"):
            cmd.extend(["--offload_model", "True", "--convert_model_dtype", "--t5_cpu"])
        if req.seed is not None:
            cmd.extend(["--base_seed", str(req.seed)])
        if req.input_image:
            cmd.extend(["--image", req.input_image])

        self._loading = True
        self._loaded_model = model
        t0 = time.time()
        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(self.repo_path) + os.pathsep + env.get("PYTHONPATH", "")
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.repo_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            assert proc.stdout is not None
            lines: list[str] = []
            for line in proc.stdout:
                lines.append(line)
                if cancel_flag():
                    proc.terminate()
                    return GenerateResult(
                        ok=False,
                        error="cancelled",
                        error_category="cancelled",
                        engine=self.name,
                    )
            rc = proc.wait()
            if rc != 0 or not out.exists():
                # Some Wan versions write to default save path — search.
                found = list(out.parent.glob("*.mp4"))
                if found and rc == 0:
                    shutil.copy2(found[-1], out)
                else:
                    return GenerateResult(
                        ok=False,
                        error="".join(lines)[-800:] or f"wan exit {rc}",
                        error_category="oom" if "out of memory" in "".join(lines).lower() else "inference_failed",
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
                meta={"backend": "wan_generate_py", "cmd": cmd[:8]},
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

    def _generate_diffusers(
        self,
        req: GenerateRequest,
        model: str,
        cancel_flag: Callable[[], bool],
    ) -> GenerateResult:
        """Use HuggingFace Diffusers Wan pipelines when official repo is absent."""
        try:
            import torch
            from diffusers import DiffusionPipeline  # type: ignore
        except Exception as exc:  # noqa: BLE001
            return GenerateResult(
                ok=False,
                error=f"Wan repo not cloned and Diffusers unavailable: {exc}",
                error_category="dependency_missing",
                engine=self.name,
            )

        if cancel_flag():
            return GenerateResult(ok=False, error="cancelled", error_category="cancelled", engine=self.name)

        hf_id = WAN_MODELS[model]["hf"] + "-Diffusers"
        # Fallback to non-diffusers id
        candidates = [hf_id, WAN_MODELS[model]["hf"]]
        self._loading = True
        t0 = time.time()
        last_err = None
        try:
            pipe = None
            loaded_id = None
            for cid in candidates:
                try:
                    pipe = DiffusionPipeline.from_pretrained(
                        cid,
                        torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
                        cache_dir=str(self.model_cache / "hf"),
                    )
                    loaded_id = cid
                    break
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
            if pipe is None:
                return GenerateResult(
                    ok=False,
                    error=f"Failed to load Wan Diffusers pipeline: {last_err}",
                    error_category="model_not_installed",
                    engine=self.name,
                )
            if torch.cuda.is_available():
                if req.low_vram:
                    try:
                        pipe.enable_model_cpu_offload()
                    except Exception:
                        pipe.to("cuda")
                else:
                    pipe.to("cuda")
            self._loaded_model = loaded_id
            w, h = self.parse_resolution(req.resolution, req.aspect_ratio)
            # Keep dims divisible by 16 for VAE.
            w, h = (w // 16) * 16, (h // 16) * 16
            num_frames = max(16, int(req.duration * min(req.fps, 24)))
            kwargs: dict[str, Any] = {
                "prompt": req.prompt,
                "negative_prompt": req.negative_prompt or None,
                "num_frames": min(num_frames, 81),
                "height": min(h, 704),
                "width": min(w, 1280),
            }
            if req.seed is not None:
                kwargs["generator"] = torch.Generator(device="cuda" if torch.cuda.is_available() else "cpu").manual_seed(
                    req.seed
                )
            if cancel_flag():
                return GenerateResult(ok=False, error="cancelled", error_category="cancelled", engine=self.name)
            result = pipe(**{k: v for k, v in kwargs.items() if v is not None})
            frames = result.frames[0] if hasattr(result, "frames") else result.videos[0]
            out = Path(req.output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            self._export_frames(frames, out, fps=min(req.fps, 24))
            return GenerateResult(
                ok=True,
                output_path=str(out),
                model=loaded_id,
                engine=self.name,
                peak_vram_mb=peak_vram_mb(),
                render_time_sec=time.time() - t0,
                meta={"backend": "diffusers"},
            )
        except torch.cuda.OutOfMemoryError as exc:
            return GenerateResult(
                ok=False,
                error=str(exc),
                error_category="oom",
                engine=self.name,
                model=model,
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
            try:
                import torch
                import gc

                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    def _export_frames(self, frames: Any, out: Path, fps: int) -> None:
        import numpy as np

        try:
            import imageio.v2 as imageio
        except ImportError:
            import imageio

        arrs = []
        for fr in frames:
            if hasattr(fr, "convert"):
                arrs.append(np.array(fr.convert("RGB")))
            else:
                a = np.array(fr)
                if a.dtype != np.uint8:
                    a = (a * 255).clip(0, 255).astype(np.uint8)
                arrs.append(a)
        imageio.mimsave(str(out), arrs, fps=fps)

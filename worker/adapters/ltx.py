"""LTX-Video adapter — genuine upstream inference.

Official: https://github.com/Lightricks/LTX-Video
Supports T2V, I2V, and video extension where upstream provides it.
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

LTX_MODELS = {
    "ltxv-2b-0.9.8-distilled": {
        "hf": "Lightricks/LTX-Video",
        "weight_hint": "ltxv-2b-0.9.8-distilled",
        "min_vram_gb": 8.0,
        "config": "configs/ltxv-2b-0.9.8-distilled.yaml",
    },
    "ltxv-13b-0.9.8-distilled": {
        "hf": "Lightricks/LTX-Video",
        "weight_hint": "ltxv-13b-0.9.8-distilled",
        "min_vram_gb": 16.0,
        "config": "configs/ltxv-13b-0.9.8-distilled.yaml",
    },
    "ltxv-13b-0.9.8-dev": {
        "hf": "Lightricks/LTX-Video",
        "weight_hint": "ltxv-13b-0.9.8-dev",
        "min_vram_gb": 24.0,
        "config": "configs/ltxv-13b-0.9.8-dev.yaml",
    },
}


class LTXAdapter(EngineAdapter):
    name = "ltx"
    upstream = "https://github.com/Lightricks/LTX-Video"
    license_note = "OpenRail-M for many checkpoints — check model license files"
    supported_tasks = ["text-to-video", "image-to-video", "video-extend"]
    min_vram_gb = 8.0

    def _weights_dir(self) -> Path:
        return self.model_cache / "ltx"

    def _find_weight_files(self) -> list[Path]:
        root = self._weights_dir()
        if not root.exists():
            return []
        return list(root.rglob("*.safetensors"))

    def _detect_installed(self) -> list[str]:
        files = self._find_weight_files()
        names = []
        joined = " ".join(str(p) for p in files)
        for name, meta in LTX_MODELS.items():
            hint = meta["weight_hint"]
            if hint in joined:
                names.append(name)
            elif any(hint in p.name for p in files):
                names.append(name)
        # HF cache
        hf = self.model_cache / "hf"
        if hf.exists():
            for p in hf.rglob("*ltxv*"):
                for name, meta in LTX_MODELS.items():
                    if meta["weight_hint"] in p.name and name not in names:
                        names.append(name)
        return sorted(set(names))

    def is_installed(self) -> bool:
        return bool(self._detect_installed()) or bool(self._find_weight_files())

    def _repo_ready(self) -> bool:
        if not self.repo_path:
            return False
        return (self.repo_path / "inference.py").exists() or (
            self.repo_path / "ltx_video"
        ).exists()

    def is_ready(self, *, cuda_available: bool, vram_gb: float | None) -> bool:
        if not cuda_available:
            return False
        if not self.is_installed():
            return False
        if vram_gb is not None and vram_gb < 6:
            return False
        return True

    def list_models(self) -> list[dict[str, Any]]:
        installed = set(self._detect_installed())
        files = self._find_weight_files()
        # If safetensors present but name unknown, mark generic installed.
        generic = bool(files) and not installed
        out = []
        for name, meta in LTX_MODELS.items():
            out.append(
                {
                    "name": name,
                    "installed": name in installed or generic,
                    "hf": meta["hf"],
                    "min_vram_gb": meta["min_vram_gb"],
                    "config": meta["config"],
                }
            )
        return out

    def _select_model(self, req: GenerateRequest) -> str | None:
        installed = self._detect_installed()
        if not installed:
            if self._find_weight_files():
                return "ltxv-2b-0.9.8-distilled"
            return None
        if req.low_vram or req.quality == "draft":
            for pref in ("ltxv-2b-0.9.8-distilled", "ltxv-13b-0.9.8-distilled"):
                if pref in installed:
                    return pref
        for pref in (
            "ltxv-13b-0.9.8-distilled",
            "ltxv-2b-0.9.8-distilled",
            "ltxv-13b-0.9.8-dev",
        ):
            if pref in installed:
                return pref
        return installed[0]

    def generate(self, req: GenerateRequest, cancel_flag: Callable[[], bool]) -> GenerateResult:
        if cancel_flag():
            return GenerateResult(ok=False, error="cancelled", error_category="cancelled", engine=self.name)
        model = self._select_model(req)
        if not model:
            return GenerateResult(
                ok=False,
                error="LTX model weights not installed",
                error_category="model_not_installed",
                engine=self.name,
            )
        if self._repo_ready():
            return self._generate_upstream(req, model, cancel_flag)
        return self._generate_diffusers(req, model, cancel_flag)

    def _generate_upstream(
        self,
        req: GenerateRequest,
        model: str,
        cancel_flag: Callable[[], bool],
    ) -> GenerateResult:
        assert self.repo_path is not None
        out = Path(req.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        config = LTX_MODELS[model]["config"]
        inference = self.repo_path / "inference.py"
        if not inference.exists():
            return GenerateResult(
                ok=False,
                error="LTX inference.py not found in repo",
                error_category="dependency_missing",
                engine=self.name,
            )

        # Upstream CLI conventions vary by version; pass common flags only.
        cmd = [
            sys.executable,
            str(inference),
            "--prompt",
            req.prompt,
            "--output_path",
            str(out),
        ]
        cfg_path = self.repo_path / config
        if cfg_path.exists():
            cmd.extend(["--config", str(cfg_path)])
        if req.seed is not None:
            cmd.extend(["--seed", str(req.seed)])
        if req.input_image:
            cmd.extend(["--input_image_path", req.input_image])
        if req.input_video and req.task == "video-extend":
            cmd.extend(["--input_video_path", req.input_video])
        if req.negative_prompt:
            cmd.extend(["--negative_prompt", req.negative_prompt])

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
                # Some scripts write into a directory
                candidates = list(out.parent.glob("**/*.mp4"))
                if candidates and rc == 0:
                    out.write_bytes(candidates[-1].read_bytes())
                else:
                    text = "".join(buf)
                    return GenerateResult(
                        ok=False,
                        error=text[-800:] or f"ltx exit {rc}",
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
                meta={"backend": "ltx_inference_py"},
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
        try:
            import torch
            from diffusers import LTXPipeline  # type: ignore
            from diffusers.utils import export_to_video  # type: ignore
        except Exception as exc:  # noqa: BLE001
            return GenerateResult(
                ok=False,
                error=f"LTX repo missing and Diffusers LTXPipeline unavailable: {exc}",
                error_category="dependency_missing",
                engine=self.name,
            )

        self._loading = True
        t0 = time.time()
        try:
            pipe = LTXPipeline.from_pretrained(
                "Lightricks/LTX-Video",
                torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
                cache_dir=str(self.model_cache / "hf"),
            )
            if torch.cuda.is_available():
                if req.low_vram:
                    try:
                        pipe.enable_model_cpu_offload()
                    except Exception:
                        pipe.to("cuda")
                else:
                    pipe.to("cuda")
            self._loaded_model = model
            w, h = self.parse_resolution(req.resolution, req.aspect_ratio)
            w, h = max(64, (w // 32) * 32), max(64, (h // 32) * 32)
            # Upstream LTX often uses 1216x704 @ 30fps; clamp for VRAM.
            if req.low_vram:
                w, h = min(w, 768), min(h, 512)
                if req.aspect_ratio == "9:16":
                    w, h = 512, 768
            num_frames = max(9, int(req.duration * 8))
            # LTX frames often need 8k+1 pattern
            num_frames = (num_frames // 8) * 8 + 1
            kwargs: dict[str, Any] = {
                "prompt": req.prompt,
                "negative_prompt": req.negative_prompt or None,
                "width": w,
                "height": h,
                "num_frames": min(num_frames, 121),
                "num_inference_steps": 8 if "distilled" in model or req.quality == "draft" else 30,
            }
            if req.seed is not None:
                kwargs["generator"] = torch.Generator(
                    device="cuda" if torch.cuda.is_available() else "cpu"
                ).manual_seed(req.seed)
            if cancel_flag():
                return GenerateResult(ok=False, error="cancelled", error_category="cancelled", engine=self.name)

            # I2V / extend when pipeline supports image argument
            if req.input_image:
                try:
                    from PIL import Image

                    kwargs["image"] = Image.open(req.input_image).convert("RGB")
                except Exception:
                    pass

            result = pipe(**{k: v for k, v in kwargs.items() if v is not None})
            frames = result.frames[0]
            out = Path(req.output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            export_to_video(frames, str(out), fps=min(req.fps, 30))
            return GenerateResult(
                ok=True,
                output_path=str(out),
                model=model,
                engine=self.name,
                peak_vram_mb=peak_vram_mb(),
                render_time_sec=time.time() - t0,
                meta={"backend": "diffusers_ltx"},
            )
        except Exception as exc:  # noqa: BLE001
            cat = "oom" if "out of memory" in str(exc).lower() else "inference_failed"
            return GenerateResult(
                ok=False,
                error=str(exc),
                error_category=cat,
                engine=self.name,
                model=model,
            )
        finally:
            self._loading = False

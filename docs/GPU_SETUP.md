# GPU Worker Setup

## Requirements

- NVIDIA GPU with recent drivers (RTX 30xx/40xx/50xx recommended for FramePack; larger VRAM for Wan A14B)
- Linux + CUDA-capable PyTorch
- FFmpeg / FFprobe
- Disk for model cache (tens of GB)

CPU-only machines can run the **gateway** only. They cannot efficiently run large diffusion/video models.

## Install worker deps

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
pip install fastapi uvicorn pydantic pydantic-settings httpx pillow numpy imageio imageio-ffmpeg
# Engine-specific deps per upstream README when cloning repos:
#   third_party/Wan2.2
#   third_party/LTX-Video
#   third_party/FramePack
```

## Clone official repos (optional but recommended)

```bash
mkdir -p third_party
git clone https://github.com/Wan-Video/Wan2.2.git third_party/Wan2.2
git clone https://github.com/Lightricks/LTX-Video.git third_party/LTX-Video
git clone https://github.com/lllyasviel/FramePack.git third_party/FramePack
```

Respect each project's license and model card terms.

## Download weights

```bash
export MODEL_CACHE=./models HF_HOME=./models/hf
# Optional: export HF_TOKEN=...
python scripts/setup_models.py --engine wan   # starts with TI2V-5B
python scripts/setup_models.py --engine ltx
python scripts/setup_models.py --engine framepack
```

## Run worker

```bash
export WORKER_TOKEN=secret MODEL_CACHE=./models ALLOW_MOCK_INFERENCE=false
python -m uvicorn worker.main:app --host 0.0.0.0 --port 8090
curl -H "X-Worker-Token: secret" http://127.0.0.1:8090/health
```

## VRAM guidance (approximate)

| Engine | Path | Rough VRAM |
|--------|------|------------|
| Wan TI2V-5B | offload + convert dtype | ≥16GB |
| Wan A14B | full | ≥80GB |
| LTX 2B distilled / fp8 | upstream | ≥8GB |
| FramePack 13B | packed context | ≥6GB |

Use routing mode `low_vram` to prefer capable low-resource engines and upstream offload flags only.

## Portable targets

Local NVIDIA · cloud GPU VM · RunPod-like · vast.ai-like · Colab-style CUDA Linux.

No commercial GPU vendor is mandatory.

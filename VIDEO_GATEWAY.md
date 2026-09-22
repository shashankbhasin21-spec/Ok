# Video Creation Gateway

**Open-model AI video generation control plane for Instagram & YouTube automation.**

This repository adds a production-ready, self-hostable **master video MCP/API gateway** plus a separate **GPU worker** that runs real generative inference with open projects:

- [Wan 2.2](https://github.com/Wan-Video/Wan2.2)
- [LTX-Video](https://github.com/Lightricks/LTX-Video)
- [FramePack](https://lllyasviel.github.io/frame_pack_gitpage/)

It is **not** a slideshow generator, Ken Burns renderer, stock assembler, or paid-API wrapper.

> **OPEN-SOURCE MODEL ≠ FREE COMPUTE.**  
> Licenses may allow self-hosting, but GPU electricity / cloud rental still costs money. Metrics separate provider API cost, estimated compute cost, and unknown compute cost — self-hosted is never falsely shown as $0.

## Architecture

```
Clients / MCP  →  Gateway (CPU)  →  Job router  →  GPU Worker
                                      ↓
                         Wan / LTX / FramePack adapters
                                      ↓
                         Scene plan → continuity → stitch → QC
                                      ↓
                         READY ≥30s social MP4 (default)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start (CPU gateway)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[video]"
cp .env.video.example .env
export API_KEY=$(python scripts/generate_gateway_api_key.py)
export AUTH_MODE=required
uvicorn gateway.main:app --host 0.0.0.0 --port 8080
curl http://127.0.0.1:8080/health
# Expect generation_available: false until a GPU worker is connected
```

Dashboard: http://127.0.0.1:8080/dashboard

## GPU worker

See [docs/GPU_SETUP.md](docs/GPU_SETUP.md).

```bash
export WORKER_TOKEN=dev-worker ALLOW_MOCK_INFERENCE=false
uvicorn worker.main:app --host 0.0.0.0 --port 8090
export VIDEO_WORKER_URL=http://127.0.0.1:8090
```

## Model install

```bash
python scripts/setup_models.py --engine wan
python scripts/verify_install.py
```

## MCP

```bash
export VIDEO_GATEWAY_URL=http://127.0.0.1:8080
export API_KEY  # same value the gateway was started with
python -m mcp_server.server
```

Docs: [docs/MCP_SETUP.md](docs/MCP_SETUP.md)

## Docker

```bash
docker compose build
docker compose up
```

Railway gateway-only: [railway.toml](railway.toml) — set `VIDEO_WORKER_URL` to a private GPU worker. The gateway never pretends it has CUDA.

## Smoke test

```bash
python scripts/smoke_test.py
```

Without a compatible GPU worker this prints:

`GENERATION BLOCKED — NO COMPATIBLE GPU WORKER`

and does **not** write a fake video.

## Tests

```bash
pytest tests/video -q
```

## Security

- API key on gateway (`API_KEY`) — generate with `scripts/generate_gateway_api_key.py`
- Worker token (`WORKER_TOKEN`)
- Placeholders (`changeme`, …) fail closed (503) when `AUTH_MODE=required`
- Render updates: `scripts/set_render_gateway_api_key.py` (per-key PUT; never wipes other env vars)
- No secrets in git — use `.env.video.example`

## Licenses

Application code: see `LICENSE`. Upstream model/code licenses (Apache-2.0, OpenRail-M, etc.) must be respected independently — check each project and model card before commercial use.

## Firm platform note

This repo also contains the existing `earner` / firm automation code. The video gateway is additive and is the **single video-generation control plane** for future Instagram/YouTube automation.

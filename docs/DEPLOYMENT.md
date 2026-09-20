# Deployment

## Local (gateway + worker)

```bash
cp .env.video.example .env
# Generate strong secrets (do not commit .env):
#   python scripts/generate_gateway_api_key.py
#   python scripts/generate_gateway_api_key.py --label WORKER_TOKEN
# Put values in .env as API_KEY=... and WORKER_TOKEN=...
# docker compose refuses to start if API_KEY / WORKER_TOKEN are unset.
docker compose up --build
```

### API_KEY management

| Action | How |
|--------|-----|
| Generate | `python scripts/generate_gateway_api_key.py` |
| Local / Compose | set `API_KEY` + `WORKER_TOKEN` in `.env`; `AUTH_MODE=required` |
| Render (per-key, no wipe) | `RENDER_API_KEY=... GATEWAY_API_KEY=... python scripts/set_render_gateway_api_key.py` |
| Clients | header `X-API-Key: $API_KEY` |

Known placeholders (`changeme`, `change-me-gateway-key`, …) are treated as missing → HTTP 503, never open access.

Or process-based:

```bash
# Terminal A — GPU machine
uvicorn worker.main:app --host 0.0.0.0 --port 8090

# Terminal B — control plane
export VIDEO_WORKER_URL=http://GPU_HOST:8090
uvicorn gateway.main:app --host 0.0.0.0 --port 8080
```

## Railway (gateway only)

Use `Dockerfile.gateway` / `railway.toml`.

**Critical:** Railway must not pretend to have GPU inference. Set:

- `VIDEO_WORKER_URL` → private GPU worker
- `API_KEY`, `WORKER_TOKEN`
- persistent volume for `/data` if using SQLite/local storage

`GET /health` → `generation_available` stays `false` until the worker is healthy with ready engines.

## Production notes

- Prefer Postgres via `DATABASE_URL`
- Keep worker endpoints off the public internet; token + private network
- Never commit weights or secrets
- Scale workers only with measured backlog and budget

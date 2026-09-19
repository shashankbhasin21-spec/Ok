# Deployment

## Local (gateway + worker)

```bash
cp .env.video.example .env
# edit secrets
docker compose up --build
```

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

# Video Gateway Architecture

## Control plane vs inference

```
AI CLIENT / ChatGPT / Automation / MCP
              |
              v
     MASTER VIDEO GATEWAY  (CPU OK — Railway / local)
       FastAPI + SQLite/Postgres + Router + Jobs
              |
              |  VIDEO_WORKER_URL + WORKER_TOKEN
              v
        GPU WORKER (CUDA required)
          adapters: Wan 2.2 | LTX-Video | FramePack
              |
              v
     REAL open-model inference
              |
              v
   Scene plan → continuity → stitch (FFmpeg) → audio → captions → QC
              |
              v
        READY asset (>=30s social default)
```

## Hard rules

1. `generation_available=true` only when a worker reports CUDA + ≥1 ready engine.
2. No silent paid providers (Runway, Higgsfield, etc.).
3. Idempotency keys prevent duplicate social assets.
4. QC failure ⇒ job `FAILED`, never `COMPLETED` / READY.
5. OPEN-SOURCE MODEL ≠ FREE COMPUTE.

## Packages

| Path | Role |
|------|------|
| `gateway/` | Public API, routing, planning, stitch/QC, metrics, dashboard |
| `worker/` | CUDA detection, model manager, engine adapters |
| `mcp_server/` | MCP tools that call the gateway only |
| `dashboard/video/` | Operator UI |
| `scripts/` | Model setup, verify, smoke test |

## Job states

`QUEUED → PLANNING → ROUTING → DOWNLOADING_MODEL → RENDERING → STITCHING → AUDIO → QC → COMPLETED | FAILED`

# API

Base URL: gateway host. Auth: header `X-API-Key: $API_KEY`.

## Endpoints

| Method | Path | Notes |
|--------|------|-------|
| GET | `/health` | Public; includes `generation_available` |
| GET | `/version` | Version |
| GET | `/v1/providers` | Auth |
| GET | `/v1/providers/health` | Auth |
| POST | `/v1/videos` | Async create |
| GET | `/v1/videos/{job_id}` | Status + social contract fields |
| GET | `/v1/videos/{job_id}/events` | Event log |
| POST | `/v1/videos/{job_id}/cancel` | Cancel |
| GET | `/v1/videos/{job_id}/download` | READY only |
| GET | `/v1/videos/{job_id}/preview` | READY inline MP4 |
| GET | `/v1/videos/{job_id}/thumbnail` | Thumbnail when present |
| GET | `/v1/jobs` | List |
| GET | `/v1/metrics` | Ops metrics |
| POST | `/v1/assets/upload` | Multipart image upload (JPEG/PNG/WebP/GIF, ≤25MB) |
| GET | `/v1/assets` | Uploaded images + READY generated videos |
| GET | `/v1/assets/{asset_id}/file` | Fetch uploaded image by id |

## Create example

```bash
curl -X POST "$GW/v1/videos" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Cinematic futuristic AI office transformation",
    "duration": 30,
    "aspect_ratio": "9:16",
    "quality": "high",
    "engine": "auto",
    "audio": true,
    "captions": true
  }'
```

Response: `{"job_id":"...","status":"QUEUED"}`

## Upload reference image

```bash
curl -X POST "$GW/v1/assets/upload" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@./still.png;type=image/png"
```

Use the returned `path` as `input_image` on `POST /v1/videos`.

## Social production contract

READY assets expose: `job_id`, `asset_id`, `duration`, `aspect_ratio`, `resolution`, `engine`, `model`, `qc_status`, `thumbnail`, `output_location`, `generation_timestamp`, `prompt_hash`, `content_hash`.

- READY ⇒ QC passed (not published)
- PENDING/SCHEDULED must never be reported as LIVE

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
| GET | `/v1/jobs` | List |
| GET | `/v1/metrics` | Ops metrics |

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

## Social production contract

READY assets expose: `job_id`, `asset_id`, `duration`, `aspect_ratio`, `resolution`, `engine`, `model`, `qc_status`, `thumbnail`, `output_location`, `generation_timestamp`, `prompt_hash`, `content_hash`.

- READY ⇒ QC passed (not published)
- PENDING/SCHEDULED must never be reported as LIVE

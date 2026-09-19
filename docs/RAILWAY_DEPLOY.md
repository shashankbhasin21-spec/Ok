# Railway deployment notes for Grey Quantum

## Status

CLI is installed in the agent environment. Deploy is **blocked** until
`RAILWAY_TOKEN` is added as a Cursor Cloud secret (or you run `railway login`
locally and invite the agent).

## Architecture (2 services)

| Service | Dockerfile | Public? | Start |
|---|---|---|---|
| `api` | `Dockerfile.api` | yes | `earner firm serve --host 0.0.0.0 --port $PORT` |
| `web` | `Dockerfile.web` | yes (custom domain) | Next.js on `$PORT` |

`web` must set:

```
FIRM_API_URL=https://<api-public-url>
```

`api` should set:

```
FIRM_OWNER_SECRET=<12+ char secret>
EARNER_WORKDIR=/data
```

Attach a Railway **volume** at `/data` on `api` so catalog/orders survive redeploys.

## Known deploy issues (checked in advance)

1. **API bound to 127.0.0.1** — fixed in Dockerfiles via `--host 0.0.0.0`.
2. **Wrong PORT** — Railway injects `$PORT`; Dockerfiles respect it.
3. **Web cannot reach API** — set `FIRM_API_URL` to the public `api` HTTPS URL (not localhost).
4. **Owner auth on CEO buttons** — use the same `FIRM_OWNER_SECRET` you set on Railway.
5. **Empty catalog after first boot** — call `POST /api/commerce/run` with owner secret and `{"publish":true}` once.
6. **Ephemeral disk** — without a volume, SQLite resets every deploy.
7. **Payment still offline** — public site ≠ settled cash until Stripe/Razorpay keys exist.

## After token is available

```bash
export RAILWAY_TOKEN=...
railway login --token "$RAILWAY_TOKEN"   # or RAILWAY_TOKEN env alone
railway init --name grey-quantum
# create api + web services in dashboard, then:
railway up --service api --dockerfile Dockerfile.api
railway up --service web --dockerfile Dockerfile.web
railway domain --service web
```

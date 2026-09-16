# Temporary public preview link

This agent can open a short-lived public HTTPS tunnel to the firm dashboard.

```bash
# API (local)
earner firm serve --host 127.0.0.1 --port 8787

# Dashboard (local, proxies /api to the firm API)
cd dashboard && FIRM_API_URL=http://127.0.0.1:8787 npm run dev -- -H 0.0.0.0 -p 3000

# Public tunnel (Cloudflare quick tunnel)
cloudflared tunnel --url http://127.0.0.1:3000
```

Open the printed `https://….trycloudflare.com` URL in Safari on your phone.

Notes:
- Quick tunnels are temporary and stop when the agent/tunnel process stops.
- Do not put live Stripe secrets into a public demo tunnel.
- For a permanent phone link, deploy to Railway/Render/Fly with env secrets.

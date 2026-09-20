#!/usr/bin/env python3
"""Set production API_KEY on Render service video-gateway-render without committing secrets.

Requires env:
  RENDER_API_KEY  — Render Account API key (rnd_...)
  GATEWAY_API_KEY — value for the gateway X-API-Key (API_KEY on the service)

Optional:
  RENDER_SERVICE_NAME — default video-gateway-render
  RENDER_API_BASE     — default https://api.render.com/v1

Never prints secret values. Keeps AUTH_MODE=required and ALLOW_UNAUTHENTICATED=false.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


API_BASE = os.environ.get("RENDER_API_BASE", "https://api.render.com/v1").rstrip("/")
SERVICE_NAME = os.environ.get("RENDER_SERVICE_NAME", "video-gateway-render")


def _req(method: str, path: str, token: str, body: dict | list | None = None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            raw = resp.read().decode() or "null"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"Render API {method} {path} failed: HTTP {exc.code}: {detail[:500]}") from exc


def _find_service(token: str) -> str:
    status, payload = _req("GET", "/services?limit=50", token)
    if status != 200 or not isinstance(payload, list):
        raise SystemExit(f"Unexpected services response HTTP {status}")
    matches = []
    for item in payload:
        svc = item.get("service", item) if isinstance(item, dict) else {}
        name = svc.get("name") or svc.get("serviceDetails", {}).get("name")
        sid = svc.get("id")
        if name == SERVICE_NAME and sid:
            matches.append(sid)
    if not matches:
        names = []
        for item in payload:
            svc = item.get("service", item) if isinstance(item, dict) else {}
            names.append(svc.get("name") or "?")
        raise SystemExit(f"Service {SERVICE_NAME!r} not found. Visible: {names[:20]}")
    return matches[0]


def main() -> int:
    render_token = os.environ.get("RENDER_API_KEY", "").strip()
    gateway_key = os.environ.get("GATEWAY_API_KEY", "").strip()
    if not render_token:
        print("RENDER_API_KEY is required (Render Account API key, rnd_...).", file=sys.stderr)
        return 2
    if not gateway_key:
        print("GATEWAY_API_KEY is required (gateway X-API-Key value).", file=sys.stderr)
        return 2

    service_id = _find_service(render_token)
    print(f"Updating env vars on {SERVICE_NAME} ({service_id})")

    env_body = [
        {"key": "API_KEY", "value": gateway_key},
        {"key": "AUTH_MODE", "value": "required"},
        {"key": "ALLOW_UNAUTHENTICATED", "value": "false"},
    ]
    # PUT replaces listed keys; Render merge endpoint varies by API version.
    status, _ = _req("PUT", f"/services/{service_id}/env-vars", render_token, env_body)
    print(f"Env update HTTP {status}")

    # Trigger deploy so the new secret is loaded.
    status, deploy = _req(
        "POST",
        f"/services/{service_id}/deploys",
        render_token,
        {"clearCache": "do_not_clear"},
    )
    deploy_id = deploy.get("id") if isinstance(deploy, dict) else None
    print(f"Deploy triggered HTTP {status} id={deploy_id or 'unknown'}")
    print("Done. Secrets were not printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Set production API_KEY on Render service video-gateway-render without committing secrets.

Requires env:
  RENDER_API_KEY  — Render Account API key (rnd_...)
  GATEWAY_API_KEY — value for the gateway X-API-Key (API_KEY on the service)

Optional:
  RENDER_SERVICE_NAME — default video-gateway-render
  RENDER_API_BASE     — default https://api.render.com/v1

Never prints secret values. Keeps AUTH_MODE=required and ALLOW_UNAUTHENTICATED=false.

IMPORTANT: updates env vars one key at a time via
  PUT /services/{id}/env-vars/{key}
Never use the collection PUT /services/{id}/env-vars — that replaces the
entire env set and would delete every other secret on the service.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
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


def _put_env_var(token: str, service_id: str, key: str, value: str) -> int:
    """Update a single env var. Never call the collection replace endpoint."""
    encoded_key = urllib.parse.quote(key, safe="")
    path = f"/services/{service_id}/env-vars/{encoded_key}"
    status, _ = _req("PUT", path, token, {"value": value})
    return status


def managed_env_updates(gateway_key: str) -> list[tuple[str, str]]:
    """Return (env_key, value) pairs this script will write — for tests."""
    return [
        ("API_KEY", gateway_key),
        ("AUTH_MODE", "required"),
        ("ALLOW_UNAUTHENTICATED", "false"),
    ]


def main() -> int:
    render_token = os.environ.get("RENDER_API_KEY", "").strip()
    gateway_key = os.environ.get("GATEWAY_API_KEY", "").strip()
    if not render_token:
        print("RENDER_API_KEY is required (Render Account API key, rnd_...).", file=sys.stderr)
        return 2
    if not gateway_key:
        print("GATEWAY_API_KEY is required (gateway X-API-Key value).", file=sys.stderr)
        return 2

    from gateway.auth import is_usable_api_key

    if not is_usable_api_key(gateway_key):
        print(
            "GATEWAY_API_KEY is empty or a known placeholder (changeme, etc.). "
            "Generate one with: python scripts/generate_gateway_api_key.py",
            file=sys.stderr,
        )
        return 2

    service_id = _find_service(render_token)
    print(f"Updating env vars on {SERVICE_NAME} ({service_id}) via per-key PUT")

    for key, value in managed_env_updates(gateway_key):
        status = _put_env_var(render_token, service_id, key, value)
        print(f"  {key}: HTTP {status}")

    # Trigger deploy so the new secret is loaded.
    status, deploy = _req(
        "POST",
        f"/services/{service_id}/deploys",
        render_token,
        {"clearCache": "do_not_clear"},
    )
    deploy_id = deploy.get("id") if isinstance(deploy, dict) else None
    print(f"Deploy triggered HTTP {status} id={deploy_id or 'unknown'}")
    print("Done. Secrets were not printed. Other service env vars were left intact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""MCP server / custom connector — calls the gateway, never duplicates inference."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

GATEWAY_URL = os.environ.get("VIDEO_GATEWAY_URL", "http://127.0.0.1:8080").rstrip("/")
API_KEY = os.environ.get("API_KEY", "")


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if API_KEY:
        h["X-API-Key"] = API_KEY
    return h


def _get(path: str) -> Any:
    with httpx.Client(timeout=60.0) as client:
        r = client.get(f"{GATEWAY_URL}{path}", headers=_headers())
        r.raise_for_status()
        return r.json()


def _post(path: str, body: dict | None = None) -> Any:
    with httpx.Client(timeout=60.0) as client:
        r = client.post(f"{GATEWAY_URL}{path}", headers=_headers(), json=body or {})
        r.raise_for_status()
        return r.json()


TOOLS = [
    {
        "name": "video_generate",
        "description": (
            "Submit a text-to-video job through the master video gateway control plane. "
            "Generation is gateway-routed to open self-hosted engines (Wan/LTX/FramePack). "
            "Never bypasses the gateway. Default social duration is >=30s unless allow_short."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "duration": {"type": "number", "default": 30},
                "aspect_ratio": {"type": "string", "default": "9:16"},
                "quality": {"type": "string", "default": "high"},
                "engine": {"type": "string", "default": "auto"},
                "audio": {"type": "boolean", "default": False},
                "captions": {"type": "boolean", "default": False},
                "negative_prompt": {"type": "string"},
                "seed": {"type": "integer"},
                "idempotency_key": {"type": "string"},
                "allow_short": {"type": "boolean", "default": False},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "video_generate_from_image",
        "description": "Image-to-video via the gateway control plane (open engines only).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "input_image": {"type": "string"},
                "duration": {"type": "number", "default": 30},
                "aspect_ratio": {"type": "string", "default": "9:16"},
                "engine": {"type": "string", "default": "auto"},
                "quality": {"type": "string", "default": "high"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["prompt", "input_image"],
        },
    },
    {
        "name": "video_extend",
        "description": "Extend an existing video through gateway routing (LTX when ready).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "input_video": {"type": "string"},
                "duration": {"type": "number", "default": 30},
                "engine": {"type": "string", "default": "ltx"},
            },
            "required": ["prompt", "input_video"],
        },
    },
    {
        "name": "video_status",
        "description": "Get job status from the gateway. READY only after QC passes.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "video_cancel",
        "description": "Cancel a gateway job (retries stay inside the original job).",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "video_download",
        "description": "Return download metadata for a READY asset (QC-passed only).",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "video_list_jobs",
        "description": "List recent video jobs from the gateway.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "status": {"type": "string"},
            },
        },
    },
    {
        "name": "video_provider_status",
        "description": "Provider readiness: AVAILABLE / MODEL_NOT_INSTALLED / GPU_UNAVAILABLE / UNHEALTHY / DISABLED.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "video_gateway_health",
        "description": "Gateway health including generation_available (true only with ready GPU worker).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "video_metrics",
        "description": "Operational metrics from the gateway (costs separated; self-hosted ≠ $0).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    if name == "video_generate":
        return _post("/v1/videos", arguments)
    if name == "video_generate_from_image":
        return _post("/v1/videos", arguments)
    if name == "video_extend":
        body = dict(arguments)
        body.setdefault("engine", "ltx")
        return _post("/v1/videos", body)
    if name == "video_status":
        return _get(f"/v1/videos/{arguments['job_id']}")
    if name == "video_cancel":
        return _post(f"/v1/videos/{arguments['job_id']}/cancel")
    if name == "video_download":
        job = _get(f"/v1/videos/{arguments['job_id']}")
        if not job.get("ready"):
            return {"error": "asset not READY", "job": job}
        return {
            "download_url": f"{GATEWAY_URL}/v1/videos/{arguments['job_id']}/download",
            "job": job,
        }
    if name == "video_list_jobs":
        q = f"/v1/jobs?limit={arguments.get('limit', 20)}"
        if arguments.get("status"):
            q += f"&status={arguments['status']}"
        return _get(q)
    if name == "video_provider_status":
        return _get("/v1/providers/health")
    if name == "video_gateway_health":
        return _get("/health")
    if name == "video_metrics":
        return _get("/v1/metrics")
    raise ValueError(f"unknown tool: {name}")


def list_tools() -> list[dict[str, Any]]:
    return TOOLS


def run_stdio_mcp() -> None:
    """Minimal JSON-RPC style MCP loop over stdin/stdout."""
    import sys

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        req_id = msg.get("id")
        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "video-creation-gateway",
                        "version": "0.1.0",
                    },
                },
            }
        elif method == "tools/list":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS},
            }
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                result = call_tool(name, args)
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(result, default=str)}]
                    },
                }
            except Exception as exc:  # noqa: BLE001
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": str(exc)},
                }
        elif method == "notifications/initialized":
            continue
        else:
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    run_stdio_mcp()

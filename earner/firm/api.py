"""HTTP API for the firm dashboard and owner controls.

Stdlib-only server so the control plane stays dependency-light.
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..payments import build_provider
from .. import config as earner_config
from .coordinator import Coordinator
from .payouts import PayoutError, PayoutStore
from .review import pipeline_metrics, run_hourly_review
from .store import FirmStore
from .vertical_slice import integration_status, load_sample_file, run_vertical_slice
from . import ASPIRATIONAL_RATE_CENTS_PER_HOUR, MILESTONE_CENTS


def make_handler(store: FirmStore, workdir: Path, payouts: PayoutStore, provider):
    coord = Coordinator(store, workdir, provider=provider)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter
            pass

        def _json(self, code: int, body: dict | list) -> None:
            raw = json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Owner-Secret, X-Payout-Session")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()
            self.wfile.write(raw)

        def _read_json(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode() or "{}")

        def do_OPTIONS(self):
            self._json(204, {})

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/health":
                return self._json(200, {"ok": True, "paused": store.is_paused()})
            if path == "/api/dashboard":
                return self._json(200, dashboard_payload(store, payouts))
            if path == "/api/opportunities":
                return self._json(200, {"items": [o.to_dict() for o in store.list_opportunities()]})
            if path == "/api/approvals":
                return self._json(200, {"items": store.pending_approvals()})
            if path == "/api/agents":
                return self._json(200, {"items": store.list_agent_runs(), "active": store.active_agent_count()})
            if path == "/api/projects":
                return self._json(200, {"items": store.list_projects()})
            if path == "/api/integrations":
                return self._json(200, integration_status())
            if path == "/api/payouts":
                return self._json(200, payouts.public_view())
            if path == "/api/audit":
                return self._json(200, {"items": store.recent_audit(100)})
            if path.startswith("/api/preview/"):
                return self._serve_preview(path.split("/api/preview/", 1)[1])
            self._json(404, {"error": "not found"})

        def _serve_preview(self, project_id: str) -> None:
            try:
                project = store.get_project(project_id)
            except KeyError:
                return self._json(404, {"error": "project not found"})
            preview = project.get("preview_path")
            if not preview or not Path(preview).exists():
                return self._json(404, {"error": "no preview"})
            data = Path(preview).read_bytes()
            ctype = "text/html" if preview.endswith(".html") else "text/markdown"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            path = urlparse(self.path).path
            body = self._read_json()
            try:
                if path == "/api/pause":
                    coord.pause()
                    return self._json(200, {"paused": True})
                if path == "/api/resume":
                    coord.resume()
                    return self._json(200, {"paused": False})
                if path == "/api/approvals/decide":
                    apr = coord.process_approval(
                        body["approval_id"],
                        approved=bool(body.get("approved")),
                        reason=body.get("reason", ""),
                    )
                    return self._json(200, apr)
                if path == "/api/opportunities/import":
                    items = body.get("opportunities") or []
                    if body.get("file"):
                        items = load_sample_file(Path(body["file"]))
                    r = coord.run_agent("opportunity_researcher", opportunities=items)
                    return self._json(200 if r.ok else 400, {"ok": r.ok, "output": r.output, "error": r.error})
                if path == "/api/opportunities/qualify":
                    r = coord.run_agent("qualification", opportunity_id=body["opportunity_id"])
                    return self._json(200 if r.ok else 400, {"ok": r.ok, "output": r.output, "error": r.error})
                if path == "/api/opportunities/propose":
                    r = coord.run_agent("proposal", opportunity_id=body["opportunity_id"])
                    return self._json(200 if r.ok else 400, {"ok": r.ok, "output": r.output, "error": r.error})
                if path == "/api/vertical-slice":
                    # Run in a subdir so repeated demos don't collide with live DB mid-request.
                    # For dashboard demo we run against the live store's workdir slice folder.
                    report = run_vertical_slice(workdir / "slice-runs" / "latest", mark_paid=bool(body.get("mark_paid", True)))
                    return self._json(200, report)
                if path == "/api/review":
                    return self._json(200, run_hourly_review(coord))
                if path == "/api/standing-auth":
                    row = store.upsert_standing_auth(
                        platform=body["platform"],
                        service=body["service"],
                        max_price_cents=int(body["max_price_cents"]),
                        daily_volume=int(body["daily_volume"]),
                        daily_spend_cents=int(body["daily_spend_cents"]),
                        enabled=bool(body.get("enabled", True)),
                    )
                    return self._json(200, row)
                if path == "/api/payouts/auth":
                    secret = self.headers.get("X-Owner-Secret") or body.get("owner_secret", "")
                    token = payouts.authenticate(secret)
                    return self._json(200, {"session_token": token, "expires_note": "single-use for next write"})
                if path == "/api/payouts":
                    session = self.headers.get("X-Payout-Session") or body.get("session_token")
                    view = payouts.save(body.get("config") or body, session)
                    return self._json(200, view)
                self._json(404, {"error": "not found"})
            except (PayoutError, KeyError, ValueError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def dashboard_payload(store: FirmStore, payouts: PayoutStore) -> dict:
    metrics = pipeline_metrics(store)
    latest = store.latest_review()
    return {
        "paused": store.is_paused(),
        "targets": {
            "aspirational_rate_usd_per_hour": ASPIRATIONAL_RATE_CENTS_PER_HOUR / 100,
            "milestone_usd": MILESTONE_CENTS / 100,
            "label": "Targets only — not guarantees or spend authorizations",
        },
        "metrics": metrics,
        "opportunities": [o.to_dict() for o in store.list_opportunities()[:50]],
        "approvals": store.pending_approvals(),
        "agents": {
            "active": store.list_agent_runs(active_only=True),
            "recent": store.list_agent_runs()[:20],
            "active_count": store.active_agent_count(),
            "max_concurrent": int(store.get_meta("max_concurrent_agents", "4")),
            "total_cost_cents": store.total_agent_cost_cents(),
        },
        "projects": store.list_projects(),
        "invoices": store.list_invoices(),
        "experiments": store.active_experiments(),
        "latest_review": latest,
        "bottleneck": (latest or {}).get("bottleneck"),
        "payouts": payouts.public_view(),
        "standing_auth": store.list_standing_auth(),
        "integrations": integration_status(),
        "simulated_data_policy": (
            "Items with simulated=true are labeled samples. "
            "Sandbox invoice settlements are not real cash."
        ),
    }


def serve(host: str = "127.0.0.1", port: int = 8787, workdir: Path | None = None):
    cfg = earner_config.load()
    workdir = Path(workdir or cfg.workdir / "firm")
    workdir.mkdir(parents=True, exist_ok=True)
    store = FirmStore(workdir / "firm.db")
    payouts = PayoutStore(workdir / "payouts.enc.json")
    try:
        provider = build_provider(cfg)
    except Exception:
        from ..payments import SandboxProvider
        provider = SandboxProvider(workdir / "sandbox_invoices.json")
    handler = make_handler(store, workdir, payouts, provider)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"firm API on http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        store.close()

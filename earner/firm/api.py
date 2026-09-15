"""HTTP API for the firm dashboard and owner controls.

Live-first: board sweeps and Stripe settlement. Simulated revenue is refused.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ..payments import SandboxProvider, build_provider
from .. import config as earner_config
from .coordinator import Coordinator
from .live_ops import (
    LiveRequired,
    apply_stripe_webhook,
    collect_live_settlements,
    live_readiness,
    require_live_provider,
    sweep_live_boards,
)
from .payouts import PayoutError, PayoutStore
from .review import pipeline_metrics, run_hourly_review
from .store import FirmStore
from .vertical_slice import integration_status, load_sample_file
from . import ASPIRATIONAL_RATE_CENTS_PER_HOUR, MILESTONE_CENTS


def make_handler(store: FirmStore, workdir: Path, payouts: PayoutStore, provider, cfg):
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
            self.send_header(
                "Access-Control-Allow-Headers",
                "Content-Type, X-Owner-Secret, X-Payout-Session, Stripe-Signature",
            )
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()
            self.wfile.write(raw)

        def _raw_body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _read_json(self) -> dict:
            raw = self._raw_body()
            if not raw:
                return {}
            return json.loads(raw.decode() or "{}")

        def do_OPTIONS(self):
            self._json(204, {})

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/health":
                return self._json(200, {
                    "ok": True,
                    "paused": store.is_paused(),
                    "live": cfg.is_live,
                    "provider": getattr(provider, "name", None),
                })
            if path == "/api/readiness":
                return self._json(200, {
                    "items": [
                        {"name": n, "ok": ok, "detail": d}
                        for n, ok, d in live_readiness(cfg)
                    ]
                })
            if path == "/api/dashboard":
                return self._json(200, dashboard_payload(store, payouts, cfg, provider))
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
            try:
                if path == "/api/webhooks/stripe":
                    payload = self._raw_body()
                    sig = self.headers.get("Stripe-Signature", "")
                    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
                    result = apply_stripe_webhook(
                        store,
                        payload=payload,
                        signature_header=sig,
                        webhook_secret=secret,
                    )
                    return self._json(200, result)

                body = json.loads(self._raw_body().decode() or "{}") if int(self.headers.get("Content-Length") or 0) else {}

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
                if path == "/api/live/sweep":
                    result = sweep_live_boards(store, workdir, cfg)
                    return self._json(200, result)
                if path == "/api/live/collect":
                    result = collect_live_settlements(store, workdir, cfg)
                    return self._json(200, result)
                if path == "/api/opportunities/import":
                    items = body.get("opportunities") or []
                    if body.get("file"):
                        items = load_sample_file(Path(body["file"]))
                    # Reject simulated imports in live mode
                    if cfg.is_live and any(i.get("simulated") for i in items):
                        return self._json(400, {"error": "simulated opportunities refused in live mode"})
                    r = coord.run_agent("opportunity_researcher", opportunities=items)
                    return self._json(200 if r.ok else 400, {"ok": r.ok, "output": r.output, "error": r.error})
                if path == "/api/opportunities/qualify":
                    r = coord.run_agent("qualification", opportunity_id=body["opportunity_id"])
                    return self._json(200 if r.ok else 400, {"ok": r.ok, "output": r.output, "error": r.error})
                if path == "/api/opportunities/propose":
                    r = coord.run_agent("proposal", opportunity_id=body["opportunity_id"])
                    return self._json(200 if r.ok else 400, {"ok": r.ok, "output": r.output, "error": r.error})
                if path == "/api/opportunities/won":
                    return self._json(200, coord.mark_won(body["opportunity_id"]))
                if path == "/api/projects/deliver":
                    plan = coord.run_agent("delivery_planner", opportunity_id=body["opportunity_id"])
                    if not plan.ok:
                        return self._json(400, {"ok": False, "error": plan.error, "output": plan.output})
                    pid = plan.output["project_id"]
                    build = coord.run_agent("engineering", project_id=pid)
                    review = coord.run_agent("independent_reviewer", project_id=pid)
                    return self._json(200, {
                        "ok": build.ok and review.ok,
                        "project_id": pid,
                        "build": build.output,
                        "review": review.output,
                        "error": build.error or review.error,
                    })
                if path == "/api/projects/invoice":
                    live = require_live_provider(cfg)
                    r = coord.run_agent(
                        "finance",
                        project_id=body["project_id"],
                        provider=live,
                        customer_email=body.get("customer_email", ""),
                    )
                    return self._json(200 if r.ok else 400, {"ok": r.ok, "output": r.output, "error": r.error})
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
                if path == "/api/vertical-slice":
                    return self._json(410, {
                        "error": "Demo slice removed from live API. Use /api/live/sweep and Stripe invoicing.",
                    })
                self._json(404, {"error": "not found"})
            except LiveRequired as exc:
                self._json(503, {"error": str(exc), "code": "live_required"})
            except (PayoutError, KeyError, ValueError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def dashboard_payload(store: FirmStore, payouts: PayoutStore, cfg, provider) -> dict:
    metrics = pipeline_metrics(store)
    latest = store.latest_review()
    ready = live_readiness(cfg)
    blocked = [r for r in ready if not r[1]]
    return {
        "paused": store.is_paused(),
        "live_mode": cfg.is_live,
        "provider": getattr(provider, "name", None),
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
        "invoices": [i for i in store.list_invoices() if not i.get("simulated")],
        "experiments": store.active_experiments(),
        "latest_review": latest,
        "bottleneck": (latest or {}).get("bottleneck"),
        "payouts": payouts.public_view(),
        "standing_auth": store.list_standing_auth(),
        "integrations": integration_status(),
        "readiness": [{"name": n, "ok": ok, "detail": d} for n, ok, d in ready],
        "blocked": [{"name": n, "detail": d} for n, ok, d in blocked],
        "simulated_data_policy": (
            "Settled cash includes only Stripe-confirmed payments. "
            "Sandbox/simulated invoices are refused and never counted as revenue."
        ),
    }


def serve(host: str = "127.0.0.1", port: int = 8787, workdir: Path | None = None):
    cfg = earner_config.load()
    workdir = Path(workdir or cfg.workdir / "firm")
    workdir.mkdir(parents=True, exist_ok=True)
    store = FirmStore(workdir / "firm.db")
    payouts = PayoutStore(workdir / "payouts.enc.json")
    provider = None
    if cfg.is_live and cfg.stripe_api_key:
        try:
            provider = build_provider(cfg)
        except Exception as exc:
            print(f"WARNING: Stripe provider failed to load: {exc}")
            provider = None
    else:
        print(
            "LIVE BLOCKED: set EARNER_MODE=live and STRIPE_API_KEY for real invoices. "
            "API will serve discovery/approvals; invoicing endpoints return 503 until configured."
        )
    handler = make_handler(store, workdir, payouts, provider, cfg)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"firm API on http://{host}:{port}  live={cfg.is_live} provider={getattr(provider, 'name', None)}")
    try:
        server.serve_forever()
    finally:
        store.close()

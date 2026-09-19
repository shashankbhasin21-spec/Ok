"""HTTP API — owner-authenticated control plane; evidence-backed commercial state."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import config as earner_config
from ..payments import build_provider
from . import ASPIRATIONAL_RATE_CENTS_PER_HOUR, BOOKINGS_TARGET_CENTS, MILESTONE_CENTS
from .connectors import connector_matrix
from .coordinator import CapacityExceeded, Coordinator, EvidenceRequired
from .forecast import build_forecast
from .live_ops import (
    LiveRequired,
    apply_stripe_webhook,
    collect_live_settlements,
    live_readiness,
    require_live_provider,
    sweep_live_boards,
)
from .offers import OFFERS
from .owner_auth import OwnerAuth, OwnerAuthError
from .payouts import PayoutError, PayoutStore
from .review import pipeline_metrics, run_hourly_review
from .store import FirmStore
from .vertical_slice import integration_status, load_sample_file
from ..commerce.engine import open_commerce, snapshot as commerce_snapshot
from ..commerce.orders import create_customer_order


def make_handler(store: FirmStore, workdir: Path, payouts: PayoutStore, provider, cfg, owner_auth: OwnerAuth):
    coord = Coordinator(store, workdir, provider=provider)
    commerce_dir = workdir / "commerce"
    commerce_store, commerce_ceo = open_commerce(commerce_dir)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _json(self, code: int, body: dict | list) -> None:
            raw = json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header(
                "Access-Control-Allow-Headers",
                "Content-Type, X-Owner-Secret, X-Owner-Session, X-Payout-Session, Stripe-Signature",
            )
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()
            self.wfile.write(raw)

        def _raw_body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _require_owner(self) -> None:
            owner_auth.authorize_secret_or_session(
                secret=self.headers.get("X-Owner-Secret"),
                session=self.headers.get("X-Owner-Session"),
                live_mode=cfg.is_live,
            )

        def do_OPTIONS(self):
            self._json(204, {})

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/health":
                return self._json(200, {
                    "ok": True, "paused": store.is_paused(),
                    "live": cfg.is_live, "provider": getattr(provider, "name", None),
                })
            if path == "/api/readiness":
                return self._json(200, {
                    "items": [{"name": n, "ok": ok, "detail": d} for n, ok, d in live_readiness(cfg)],
                    "connectors": connector_matrix(cfg, payout_configured=bool(payouts.public_view().get("configured"))),
                })
            if path == "/api/dashboard":
                return self._json(200, dashboard_payload(store, payouts, cfg, provider, coord))
            if path == "/api/commercial":
                return self._json(200, {"snapshot": store.commercial_snapshot(), "forecast": build_forecast(store)})
            if path == "/api/offers":
                return self._json(200, {"offers": [o.__dict__ for o in OFFERS], "capacity": coord.delivery_capacity()})
            if path == "/api/opportunities":
                return self._json(200, {"items": [o.to_dict() for o in store.list_opportunities() if not o.simulated]})
            if path == "/api/approvals":
                return self._json(200, {"items": store.pending_approvals()})
            if path == "/api/agents":
                return self._json(200, {"items": store.list_agent_runs(), "active": store.active_agent_count()})
            if path == "/api/projects":
                return self._json(200, {"items": store.list_projects()})
            if path == "/api/integrations":
                return self._json(200, {
                    "legacy": integration_status(),
                    "connectors": connector_matrix(cfg, payout_configured=bool(payouts.public_view().get("configured"))),
                })
            if path == "/api/payouts":
                return self._json(200, payouts.public_view())
            if path == "/api/audit":
                try:
                    self._require_owner()
                except OwnerAuthError as exc:
                    return self._json(401, {"error": str(exc)})
                return self._json(200, {"items": store.recent_audit(100)})
            if path.startswith("/api/preview/"):
                return self._serve_preview(path.split("/api/preview/", 1)[1])
            if path == "/api/commerce":
                return self._json(200, commerce_snapshot(commerce_store, commerce_ceo))
            if path == "/api/commerce/products":
                qs = parse_qs(urlparse(self.path).query)
                published = (qs.get("published") or ["0"])[0] == "1"
                items = commerce_store.list_products(published_only=published)
                return self._json(200, {"items": items})
            if path.startswith("/api/commerce/products/"):
                slug = path.split("/api/commerce/products/", 1)[1]
                try:
                    return self._json(200, commerce_store.get_product_by_slug(slug))
                except KeyError:
                    return self._json(404, {"error": "product not found"})
            if path == "/api/commerce/ceo":
                return self._json(200, commerce_ceo.status())
            if path.startswith("/api/commerce/pages/"):
                return self._serve_commerce_page(path.split("/api/commerce/pages/", 1)[1])
            self._json(404, {"error": "not found"})

        def _serve_commerce_page(self, slug: str) -> None:
            try:
                product = commerce_store.get_product_by_slug(slug)
            except KeyError:
                return self._json(404, {"error": "product not found"})
            path = product.get("page_html_path")
            if not path or not Path(path).exists():
                return self._json(404, {"error": "no product page yet — run webpage developer"})
            data = Path(path).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

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
                raw = self._raw_body()
                if path == "/api/webhooks/stripe":
                    result = apply_stripe_webhook(
                        store,
                        payload=raw,
                        signature_header=self.headers.get("Stripe-Signature", ""),
                        webhook_secret=os.environ.get("STRIPE_WEBHOOK_SECRET", ""),
                    )
                    return self._json(200, result)

                body = json.loads(raw.decode() or "{}") if raw else {}

                if path == "/api/owner/login":
                    token = owner_auth.authenticate(
                        self.headers.get("X-Owner-Secret") or body.get("owner_secret"),
                        live_mode=cfg.is_live,
                    )
                    return self._json(200, {"session": token, "expires_in_sec": 3600})

                if path == "/api/commerce/checkout":
                    try:
                        order = create_customer_order(
                            commerce_store,
                            product_id=body.get("product_id"),
                            product_slug=body.get("product_slug"),
                            customer_email=body.get("customer_email") or "",
                            customer_name=body.get("customer_name") or "",
                            ship_country=body.get("ship_country") or "IN",
                        )
                    except (ValueError, KeyError) as exc:
                        return self._json(400, {"error": str(exc)})
                    return self._json(200, {
                        "order": order,
                        "next": (
                            "Order reserved as pending_payment. Connect Stripe or Razorpay, "
                            "collect payment, then owner marks paid with provider_ref. "
                            "Indian Kotak/UPI payout details are configured later by the owner."
                        ),
                    })

                self._require_owner()

                if path == "/api/pause":
                    coord.pause()
                    return self._json(200, {"paused": True})
                if path == "/api/resume":
                    coord.resume()
                    return self._json(200, {"paused": False})
                if path == "/api/approvals/decide":
                    return self._json(200, coord.process_approval(
                        body["approval_id"], approved=bool(body.get("approved")), reason=body.get("reason", ""),
                    ))
                if path == "/api/live/sweep":
                    return self._json(200, sweep_live_boards(store, workdir, cfg))
                if path == "/api/live/collect":
                    return self._json(200, collect_live_settlements(store, workdir, cfg))
                if path == "/api/opportunities/import":
                    items = body.get("opportunities") or []
                    if body.get("file"):
                        items = load_sample_file(Path(body["file"]))
                    if any(i.get("simulated") for i in items):
                        return self._json(400, {"error": "simulated opportunities refused"})
                    r = coord.run_agent("opportunity_researcher", opportunities=items)
                    return self._json(200 if r.ok else 400, {"ok": r.ok, "output": r.output, "error": r.error})
                if path == "/api/opportunities/qualify":
                    r = coord.run_agent("qualification", opportunity_id=body["opportunity_id"])
                    return self._json(200 if r.ok else 400, {"ok": r.ok, "output": r.output, "error": r.error})
                if path == "/api/opportunities/propose":
                    r = coord.run_agent("proposal", opportunity_id=body["opportunity_id"])
                    return self._json(200 if r.ok else 400, {"ok": r.ok, "output": r.output, "error": r.error})
                if path == "/api/opportunities/submit":
                    return self._json(200, coord.record_external_submission(
                        body["opportunity_id"],
                        evidence_kind=body["evidence_kind"],
                        evidence_ref=body["evidence_ref"],
                        note=body.get("note", ""),
                    ))
                if path == "/api/opportunities/reply":
                    return self._json(200, coord.record_buyer_reply(
                        body["opportunity_id"],
                        evidence_kind=body["evidence_kind"],
                        evidence_ref=body["evidence_ref"],
                        note=body.get("note", ""),
                    ))
                if path == "/api/opportunities/sign":
                    return self._json(200, coord.mark_signed(
                        body["opportunity_id"],
                        evidence_kind=body["evidence_kind"],
                        evidence_ref=body["evidence_ref"],
                        note=body.get("note", ""),
                        offer_id=body.get("offer_id"),
                    ))
                if path == "/api/projects/deliver":
                    plan = coord.run_agent("delivery_planner", opportunity_id=body["opportunity_id"])
                    if not plan.ok:
                        return self._json(400, {"ok": False, "error": plan.error})
                    pid = plan.output["project_id"]
                    build = coord.run_agent("engineering", project_id=pid)
                    review = coord.run_agent("independent_reviewer", project_id=pid)
                    return self._json(200, {"ok": build.ok and review.ok, "project_id": pid,
                                           "build": build.output, "review": review.output})
                if path == "/api/projects/invoice":
                    live = require_live_provider(cfg)
                    r = coord.run_agent("finance", project_id=body["project_id"], provider=live,
                                       customer_email=body.get("customer_email", ""))
                    return self._json(200 if r.ok else 400, {"ok": r.ok, "output": r.output, "error": r.error})
                if path == "/api/review":
                    return self._json(200, run_hourly_review(coord))
                if path == "/api/standing-auth":
                    return self._json(200, store.upsert_standing_auth(
                        platform=body["platform"], service=body["service"],
                        max_price_cents=int(body["max_price_cents"]),
                        daily_volume=int(body["daily_volume"]),
                        daily_spend_cents=int(body["daily_spend_cents"]),
                        enabled=bool(body.get("enabled", True)),
                    ))
                if path == "/api/payouts/auth":
                    token = owner_auth.authenticate(
                        self.headers.get("X-Owner-Secret") or body.get("owner_secret"),
                        live_mode=cfg.is_live,
                    )
                    return self._json(200, {"session_token": token})
                if path == "/api/payouts":
                    session = (
                        self.headers.get("X-Payout-Session")
                        or body.get("session_token")
                        or self.headers.get("X-Owner-Session")
                    )
                    if not session:
                        session = owner_auth.authenticate(
                            self.headers.get("X-Owner-Secret"), live_mode=cfg.is_live
                        )
                    return self._json(200, payouts.save(body.get("config") or body, session))
                if path == "/api/expenses":
                    return self._json(200, store.record_expense(
                        int(body["amount_cents"]), body["category"], body.get("note", ""),
                    ))
                if path == "/api/vertical-slice":
                    return self._json(410, {"error": "Demo removed. Use live sweep + evidence-backed transitions."})
                if path == "/api/commerce/run":
                    publish = bool(body.get("publish", False))
                    result = commerce_ceo.run_company_day(
                        publish=publish,
                        outreach=bool(body.get("outreach", True)),
                    )
                    return self._json(200, result)
                if path == "/api/commerce/directive":
                    d = commerce_ceo.direct(
                        body.get("directive") or "",
                        priority=body.get("priority") or "high",
                    )
                    return self._json(200, d)
                if path == "/api/commerce/orders/mark-paid":
                    order = commerce_store.mark_order_paid(
                        body["order_id"],
                        provider=body.get("provider") or "",
                        provider_ref=body.get("provider_ref") or "",
                    )
                    return self._json(200, order)
                self._json(404, {"error": "not found"})
            except OwnerAuthError as exc:
                self._json(401, {"error": str(exc), "code": "owner_auth"})
            except LiveRequired as exc:
                self._json(503, {"error": str(exc), "code": "live_required"})
            except (EvidenceRequired, CapacityExceeded, PayoutError, KeyError, ValueError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def dashboard_payload(store, payouts, cfg, provider, coord) -> dict:
    metrics = pipeline_metrics(store)
    commercial = store.commercial_snapshot()
    forecast = build_forecast(store)
    latest = store.latest_review()
    connectors = connector_matrix(cfg, payout_configured=bool(payouts.public_view().get("configured")))
    return {
        "paused": store.is_paused(),
        "live_mode": cfg.is_live,
        "provider": getattr(provider, "name", None),
        "targets": {
            "bookings_target_usd": BOOKINGS_TARGET_CENTS / 100,
            "bookings_deadline": forecast["bookings_deadline"],
            "cash_milestone_usd": MILESTONE_CENTS / 100,
            "aspirational_rate_usd_per_hour": ASPIRATIONAL_RATE_CENTS_PER_HOUR / 100,
            "label": "Targets only — not guarantees. Bookings ≠ cash.",
        },
        "commercial": commercial,
        "forecast": forecast,
        "metrics": metrics,
        "capacity": coord.delivery_capacity(),
        "offers": [
            {"id": o.id, "name": o.name, "price_low": o.price_cents_low, "price_high": o.price_cents_high}
            for o in OFFERS
        ],
        "opportunities": [o.to_dict() for o in store.list_opportunities() if not o.simulated][:50],
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
        "bottleneck": forecast.get("main_blocker") or (latest or {}).get("bottleneck"),
        "payouts": payouts.public_view(),
        "standing_auth": store.list_standing_auth(),
        "connectors": connectors,
        "integrations": integration_status(),
        "simulated_data_policy": (
            "Production dashboard excludes simulated records. "
            "Signed bookings and cash require external evidence / provider confirmation."
        ),
    }


def serve(host: str = "127.0.0.1", port: int = 8787, workdir: Path | None = None):
    cfg = earner_config.load()
    workdir = Path(workdir or cfg.workdir / "firm")
    workdir.mkdir(parents=True, exist_ok=True)
    store = FirmStore(workdir / "firm.db")
    payouts = PayoutStore(workdir / "payouts.enc.json")
    owner_auth = OwnerAuth()
    provider = None
    if cfg.is_live and cfg.stripe_api_key:
        try:
            provider = build_provider(cfg)
        except Exception as exc:
            print(f"WARNING: Stripe provider failed: {exc}")
    else:
        print("BLOCKED: EARNER_MODE=live + STRIPE_API_KEY required for invoicing (no sandbox fallback).")
    if cfg.is_live and owner_auth.is_dev_default():
        print("BLOCKED: set FIRM_OWNER_SECRET (non-default) before live owner controls.")
    handler = make_handler(store, workdir, payouts, provider, cfg, owner_auth)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"firm API http://{host}:{port} live={cfg.is_live} provider={getattr(provider, 'name', None)}")
    try:
        server.serve_forever()
    finally:
        store.close()

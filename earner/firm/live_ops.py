"""Live firm operations — real boards, real Stripe, no simulated revenue.

Sandbox/demo paths are refused when the firm is in live mode. Settlement is
recorded only from provider-confirmed (non-sandbox) payment events.
"""

from __future__ import annotations

import time
from pathlib import Path

from .. import config as earner_config
from ..payments import SandboxProvider, StripeProvider, build_provider
from ..watch import FREELANCER_KEYWORDS, Opening, Profile, freelancer_sources, upwork_source
from .coordinator import Coordinator
from .store import FirmStore


class LiveRequired(RuntimeError):
    """Raised when live rails are required but credentials/mode are missing."""


FIRM_LIVE_PROFILE = Profile(
    strong=[
        "website", "landing page", "landing", "html", "css", "react", "next.js",
        "nextjs", "typescript", "javascript", "workflow", "automation", "n8n",
        "zapier", "python", "bug fix", "fix", "api", "fastapi",
    ],
    weak=["web", "frontend", "backend", "integration", "dashboard", "form"],
    avoid=[
        "model training", "fine-tune", "gpu", "solidity", "blockchain",
        "trading bot", "crypto trading", "mobile app from scratch",
        "fake review", "captcha",
    ],
    floor_usd=100.0,
    ceiling_bids=45,
)


def require_live_provider(cfg=None):
    """Return a real Stripe provider or raise. Never silently falls back."""
    cfg = cfg or earner_config.load()
    if not cfg.is_live:
        raise LiveRequired(
            "EARNER_MODE=live is required for live payments. "
            "Sandbox invoices are disabled in the live firm path."
        )
    if not cfg.stripe_api_key:
        raise LiveRequired(
            "STRIPE_API_KEY is missing. Add your Stripe secret key to .env "
            "(sk_live_… for production, sk_test_… for Stripe test mode)."
        )
    if cfg.stripe_api_key.startswith("sk_test_"):
        # Test mode is Stripe-real (not our sandbox), but not production cash.
        pass
    provider = build_provider(cfg)
    if isinstance(provider, SandboxProvider):
        raise LiveRequired("build_provider returned sandbox — refuse live firm ops")
    return provider


def openings_to_opportunities(openings: list[Opening]) -> list[dict]:
    out = []
    for o in openings:
        high = o.budget_high or o.budget_low
        cents = int(round(high * 100)) if high else None
        out.append({
            "source": o.source or "board",
            "external_id": o.ref,
            "title": o.title,
            "source_url": o.url,
            "description": o.description or "",
            "posted_at": o.posted_at or None,
            "budget_cents": cents,
            "budget_currency": (o.currency or "usd").lower(),
            "skills": list(o.skills or []),
            "client_signals": (
                f"country={o.client_country}; payment_verified={o.payment_verified}; "
                f"bids={o.bids}"
            ),
            "eligibility": "",
            "simulated": False,
        })
    return out


def sweep_live_boards(store: FirmStore, workdir: Path, cfg=None, *, limit: int = 40) -> dict:
    """Fetch live Freelancer RSS (+ Upwork if connected), import, qualify, propose."""
    cfg = cfg or earner_config.load()
    coord = Coordinator(store, workdir, provider=None)
    sources = freelancer_sources(
        keywords=("website", "landing page", "automation", "n8n", "python", "react")
        + FREELANCER_KEYWORDS[:4]
    )
    try:
        sources.append(upwork_source(cfg))
    except Exception:
        pass

    openings: list[Opening] = []
    errors: list[str] = []
    for src in sources:
        try:
            openings.extend(src() or [])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{getattr(src, '__name__', src)}: {exc}")

    # Deduplicate by URL within this sweep
    seen = set()
    unique = []
    for o in openings:
        if not o.url or o.url in seen:
            continue
        seen.add(o.url)
        unique.append(o)

    # Prefer fresh + in-capability
    unique.sort(key=lambda o: o.freshness(), reverse=True)
    batch = openings_to_opportunities(unique[:limit])

    imported = coord.run_agent("opportunity_researcher", opportunities=batch)
    results = {"imported": imported.output, "errors": errors, "fetched": len(unique), "qualified": [], "proposed": [], "rejected": []}

    for oid in (imported.output or {}).get("imported_ids") or []:
        q = coord.run_agent("qualification", opportunity_id=oid)
        if q.output.get("qualified"):
            results["qualified"].append(oid)
            p = coord.run_agent("proposal", opportunity_id=oid)
            if p.ok:
                results["proposed"].append({"opportunity_id": oid, "approval_id": p.output.get("approval_id")})
        else:
            results["rejected"].append({"opportunity_id": oid, "reasons": q.output.get("reasons")})

    store.audit("live_ops", "sweep", fetched=len(unique), imported=(imported.output or {}).get("count", 0))
    return results


def collect_live_settlements(store: FirmStore, workdir: Path, cfg=None) -> dict:
    """Poll Stripe for unpaid firm invoices and record only provider-confirmed cash."""
    cfg = cfg or earner_config.load()
    provider = require_live_provider(cfg)
    from .agents import FinanceAgent
    from .authorization import Authorization

    auth = Authorization(store)
    fin = FinanceAgent(store, auth, workdir=workdir)
    settled = []
    pending = []
    for inv in store.list_invoices():
        if inv.get("simulated") or inv.get("provider") == "sandbox":
            continue
        if inv.get("lifecycle") == "settled":
            continue
        result = fin.check_settlement(inv["id"], provider)
        if result.output.get("settled"):
            settled.append({"invoice_id": inv["id"], "amount_cents": result.output.get("amount_cents")})
        else:
            pending.append({"invoice_id": inv["id"], "lifecycle": inv.get("lifecycle")})
    return {"provider": provider.name, "settled": settled, "pending": pending}


def apply_stripe_webhook(
    store: FirmStore,
    *,
    payload: bytes,
    signature_header: str,
    webhook_secret: str,
) -> dict:
    """Verify Stripe signature and settle matching firm invoices idempotently."""
    if not webhook_secret:
        raise LiveRequired("STRIPE_WEBHOOK_SECRET is required to accept webhooks")
    try:
        import stripe
    except ImportError as exc:
        raise LiveRequired("pip install 'earner[stripe]'") from exc

    event = stripe.Webhook.construct_event(payload, signature_header, webhook_secret)
    if event["type"] not in ("invoice.paid", "invoice.payment_succeeded"):
        return {"handled": False, "type": event["type"]}

    obj = event["data"]["object"]
    provider_ref = obj["id"]
    amount = int(obj.get("amount_paid") or 0)
    currency = (obj.get("currency") or "usd").lower()
    paid_at = (obj.get("status_transitions") or {}).get("paid_at") or event["created"]
    event_id = f"{provider_ref}:{paid_at}"

    inv = next(
        (i for i in store.list_invoices() if i["provider_ref"] == provider_ref and i["provider"] == "stripe"),
        None,
    )
    if not inv:
        return {"handled": False, "reason": "invoice not in firm store", "provider_ref": provider_ref}
    if inv.get("simulated"):
        raise LiveRequired("refusing to settle a simulated invoice via Stripe webhook")

    applied = store.confirm_payment(inv["id"], event_id, amount, currency)
    if applied:
        project = store.get_project(inv["project_id"])
        from .models import OppStatus
        opp = store.get_opportunity(project["opportunity_id"])
        if opp.status == OppStatus.INVOICED.value:
            store.advance(project["opportunity_id"], OppStatus.PAID)
    return {
        "handled": True,
        "applied": applied,
        "invoice_id": inv["id"],
        "amount_cents": amount,
        "event_id": event_id,
    }


def live_readiness(cfg=None) -> list[tuple[str, bool, str]]:
    cfg = cfg or earner_config.load()
    import os

    stripe_sdk = _stripe_installed()
    if cfg.is_live and cfg.stripe_api_key and stripe_sdk:
        try:
            p = build_provider(cfg)
            stripe_ok = isinstance(p, StripeProvider)
            mode = "test" if cfg.stripe_api_key.startswith("sk_test_") else "live"
            stripe_detail = f"StripeProvider ready ({mode} key)" if stripe_ok else "provider is not Stripe"
        except Exception as exc:
            stripe_ok = False
            stripe_detail = str(exc)
    else:
        stripe_ok = False
        stripe_detail = "Set EARNER_MODE=live and STRIPE_API_KEY; pip install 'earner[stripe]'"

    return [
        ("EARNER_MODE=live", cfg.is_live, cfg.mode),
        ("STRIPE_API_KEY", bool(cfg.stripe_api_key), stripe_detail if cfg.stripe_api_key else "missing"),
        ("Stripe provider", stripe_ok, stripe_detail),
        ("Stripe SDK", stripe_sdk, "pip install 'earner[stripe]'"),
        ("Webhook secret", bool(os.environ.get("STRIPE_WEBHOOK_SECRET")), "needed for push settlement"),
        ("Upwork OAuth", bool(cfg.upwork_client_id and cfg.upwork_client_secret), "optional discovery"),
        ("Freelancer RSS", True, "live read-only — no key required"),
        ("ANTHROPIC_API_KEY", bool(cfg.anthropic_api_key), "optional — proposals use templates without it"),
    ]


def _stripe_installed() -> bool:
    try:
        import stripe  # noqa: F401
        return True
    except ImportError:
        return False


def assert_not_sandbox_settlement(invoice_row: dict) -> None:
    if invoice_row.get("simulated") or invoice_row.get("provider") == "sandbox":
        raise LiveRequired(
            "Refusing to count sandbox/simulated invoice as settled cash"
        )

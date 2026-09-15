"""End-to-end workflow helpers.

Live settlement requires Stripe. This module no longer marks sandbox invoices
paid — simulated cash is refused by the ledger.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .coordinator import Coordinator
from .review import pipeline_metrics, run_hourly_review
from .store import FirmStore


def run_pipeline_to_invoice(
    workdir: Path,
    opportunity: dict,
    *,
    provider,
    customer_email: str,
    auto_approve: bool = False,
    submission_evidence: dict | None = None,
    sign_evidence: dict | None = None,
) -> dict:
    """Import → qualify → propose → approve → submit(evidence) → sign(evidence) → deliver → invoice.

    Does not settle. Settlement is Stripe-only via collect/webhook.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    store = FirmStore(workdir / "firm.db")
    coord = Coordinator(store, workdir, provider=provider)
    steps: list[dict] = []

    opp_payload = {**opportunity, "simulated": bool(opportunity.get("simulated", False))}
    r = coord.run_agent("opportunity_researcher", opportunities=[opp_payload])
    steps.append({"step": "import", "ok": r.ok, "error": r.error, "output": r.output})
    ids = (r.output or {}).get("imported_ids") or []
    if not ids:
        existing = [
            o for o in store.list_opportunities()
            if o.external_id == opportunity["external_id"]
        ]
        if not existing:
            store.close()
            return {"ok": False, "steps": steps, "error": "import failed"}
        oid = existing[0].id
    else:
        oid = ids[0]

    r = coord.run_agent("qualification", opportunity_id=oid)
    steps.append({"step": "qualify", "ok": r.ok, "output": r.output, "error": r.error})
    if not (r.output or {}).get("qualified"):
        store.close()
        return {"ok": False, "steps": steps, "error": "not qualified"}

    r = coord.run_agent("proposal", opportunity_id=oid)
    steps.append({"step": "proposal", "ok": r.ok, "output": r.output, "error": r.error})
    approval_id = (r.output or {}).get("approval_id")

    if auto_approve and approval_id:
        apr = coord.process_approval(approval_id, approved=True, reason="owner approved")
        steps.append({"step": "approve", "ok": apr["status"] == "approved"})
    else:
        out = {
            "ok": True,
            "awaiting_approval": True,
            "opportunity_id": oid,
            "approval_id": approval_id,
            "steps": steps,
            "metrics": pipeline_metrics(store),
        }
        store.close()
        return out

    if not submission_evidence or not sign_evidence:
        store.close()
        return {
            "ok": True,
            "awaiting_external_evidence": True,
            "opportunity_id": oid,
            "steps": steps,
            "note": "Provide board submission + signed contract evidence before delivery/invoice",
        }

    coord.record_external_submission(oid, **submission_evidence)
    coord.record_buyer_reply(
        oid,
        evidence_kind="buyer_reply_url",
        evidence_ref=sign_evidence.get("evidence_ref", "reply"),
        note="bundled with sign evidence path",
    )
    won = coord.mark_signed(oid, **sign_evidence)
    steps.append({"step": "signed", "ok": won["status"] == "won", "status": won["status"]})

    r = coord.run_agent("delivery_planner", opportunity_id=oid)
    steps.append({"step": "plan", "ok": r.ok, "output": r.output, "error": r.error})
    project_id = (r.output or {}).get("project_id")

    r = coord.run_agent("engineering", project_id=project_id)
    steps.append({"step": "build", "ok": r.ok, "output": r.output, "error": r.error})
    preview = (r.output or {}).get("preview")

    r = coord.run_agent("independent_reviewer", project_id=project_id)
    steps.append({"step": "review", "ok": r.ok, "output": r.output, "error": r.error})

    r = coord.run_agent(
        "finance",
        project_id=project_id,
        provider=provider,
        customer_email=customer_email,
    )
    steps.append({"step": "invoice", "ok": r.ok, "output": r.output, "error": r.error})

    summary = {
        "ok": all(s.get("ok", True) for s in steps) and r.ok,
        "opportunity_id": oid,
        "project_id": project_id,
        "preview": preview,
        "invoice": r.output,
        "steps": steps,
        "metrics": pipeline_metrics(store),
        "integrations": integration_status(),
    }
    (workdir / "pipeline_report.json").write_text(json.dumps(summary, indent=2, default=str))
    store.close()
    return summary


# Kept for tests that exercise the state machine without Stripe.
# Does NOT settle — confirm_payment refuses sandbox.
def run_vertical_slice(workdir: Path, *, mark_paid: bool = False) -> dict:
    """Deterministic pipeline test helper. Settlement is disabled (mark_paid ignored)."""
    from ..payments import SandboxProvider

    sample = {
        "source": "sample",
        "external_id": "sample-landing-acme-2026",
        "title": "Acme Logistics — Landing page for freight quote desk",
        "source_url": "https://example.invalid/sample/acme-landing",
        "description": (
            "Need a clean landing page for our freight quote desk. "
            "Must explain turnaround time, capture email leads, and work on mobile."
        ),
        "budget_cents": 180_000,
        "budget_currency": "usd",
        "scope": "Single landing page with hero, CTA, and lead form placeholder",
        "skills": ["landing page", "html", "css", "website"],
        "simulated": True,
    }
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    store = FirmStore(workdir / "firm.db")
    provider = SandboxProvider(workdir / "sandbox_invoices.json")
    coord = Coordinator(store, workdir, provider=provider)
    steps: list[dict] = []

    r = coord.run_agent("opportunity_researcher", opportunities=[sample])
    steps.append({"step": "import", "ok": r.ok, "error": r.error})
    oid = (r.output or {}).get("imported_ids", [None])[0]
    if not oid:
        existing = [o for o in store.list_opportunities() if o.external_id == sample["external_id"]]
        oid = existing[0].id if existing else None
    if not oid:
        store.close()
        return {"ok": False, "steps": steps, "error": "import failed"}

    # dedupe
    r2 = coord.run_agent("opportunity_researcher", opportunities=[sample])
    steps.append({"step": "import_dedupe", "ok": r2.ok, "count": r2.output.get("count", 0)})

    r = coord.run_agent("qualification", opportunity_id=oid)
    steps.append({"step": "qualify", "ok": r.ok, "output": r.output})
    if not r.output.get("qualified"):
        store.close()
        return {"ok": False, "steps": steps, "error": "not qualified", "metrics": pipeline_metrics(store)}

    r = coord.run_agent("proposal", opportunity_id=oid)
    steps.append({"step": "proposal", "ok": r.ok})
    approval_id = r.output.get("approval_id")
    coord.process_approval(approval_id, approved=True, reason="test approval")
    steps.append({"step": "approve", "ok": True})
    assert store.get_opportunity(oid).status == "approved"
    # Do not auto-mark submitted/won/paid — that requires external evidence.
    steps.append({"step": "note", "ok": True, "detail": "awaiting external submission evidence"})

    # Prove sandbox settlement is refused if attempted
    settle_error = None
    if mark_paid:
        handle = provider.create_invoice(
            customer_email="buyer@acme.example", description="test",
            amount_cents=1000, currency="usd",
        )
        inv = store.record_firm_invoice(
            project_id="prj_test", provider=handle.provider, provider_ref=handle.provider_ref,
            amount_cents=1000, currency="usd", lifecycle="pending", url=handle.url, simulated=True,
        )
        provider.mark_paid(handle.provider_ref)
        try:
            store.confirm_payment(inv["id"], f"{handle.provider_ref}:x", 1000, "usd")
        except ValueError as exc:
            settle_error = str(exc)
        steps.append({"step": "settle_refused", "ok": settle_error is not None, "error": settle_error})

    review = run_hourly_review(coord)
    steps.append({"step": "hourly_review", "ok": True, "bottleneck": review["bottleneck"]})
    metrics = pipeline_metrics(store)
    summary = {
        "ok": True,
        "opportunity_id": oid,
        "project_id": None,
        "preview": None,
        "invoice_id": None,
        "settled_simulated": False,
        "settle_refused": settle_error,
        "steps": steps,
        "metrics": metrics,
        "latest_review": store.latest_review(),
        "integrations": integration_status(),
        "commercial": store.commercial_snapshot(),
    }
    assert metrics["finance_usd"]["gross_revenue_cents"] == 0
    assert summary["commercial"]["signed_bookings_cents"] == 0
    (workdir / "vertical_slice_report.json").write_text(json.dumps(summary, indent=2, default=str))
    store.close()
    return summary


def integration_status() -> dict:
    from .live_ops import live_readiness
    rows = live_readiness()
    status = {name: ("ready" if ok else f"blocked — {detail}") for name, ok, detail in rows}
    status["proposal_submit"] = "manual handoff only — no official submit API"
    status["payout_config"] = "encrypted local store — configure via dashboard"
    return status


def load_sample_file(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return data
    if "opportunities" in data:
        return data["opportunities"]
    out = []
    for i, lead in enumerate(data.get("leads") or []):
        out.append({
            "source": lead.get("source", "freelancer"),
            "external_id": lead.get("website") or f"lead-{i}",
            "title": lead.get("company", "Untitled"),
            "source_url": lead.get("website", ""),
            "description": lead.get("evidence") or lead.get("notes", ""),
            "skills": lead.get("tags") or ["automation"],
            "client_signals": lead.get("notes", ""),
            "budget_cents": 150_000,
            "budget_currency": "usd",
            "simulated": True,
        })
    return out

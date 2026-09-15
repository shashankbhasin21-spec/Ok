"""End-to-end vertical slice — sample opportunity through payment state."""

from __future__ import annotations

import json
from pathlib import Path

from ..payments import SandboxProvider
from .coordinator import Coordinator
from .review import pipeline_metrics, run_hourly_review
from .store import FirmStore


SAMPLE_OPPORTUNITY = {
    "source": "sample",
    "external_id": "sample-landing-acme-2026",
    "title": "Acme Logistics — Landing page for freight quote desk",
    "source_url": "https://example.invalid/sample/acme-landing",
    "description": (
        "Need a clean landing page for our freight quote desk. "
        "Must explain turnaround time, capture email leads, and work on mobile. "
        "No mobile app from scratch — web landing page only."
    ),
    "budget_cents": 180_000,
    "budget_currency": "usd",
    "scope": "Single landing page with hero, CTA, and lead form placeholder",
    "deadline": "10 days",
    "skills": ["landing page", "html", "css", "website"],
    "client_signals": "Small logistics firm, previously hired for brochure site",
    "eligibility": "Open to freelancers worldwide",
    "simulated": True,
}


def run_vertical_slice(workdir: Path, *, mark_paid: bool = True) -> dict:
    """
    import → qualify → draft proposal → approve → record submission →
    create project → demo deliverable → review → invoice → (optional) settle.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    store = FirmStore(workdir / "firm.db")
    provider = SandboxProvider(workdir / "sandbox_invoices.json")
    coord = Coordinator(store, workdir, provider=provider)

    steps: list[dict] = []

    # 1. Import labeled sample
    r = coord.run_agent("opportunity_researcher", opportunities=[SAMPLE_OPPORTUNITY])
    steps.append({"step": "import", **_sum(r)})
    if not r.ok or not r.output.get("imported_ids"):
        # Already imported — find existing
        existing = [o for o in store.list_opportunities() if o.external_id == SAMPLE_OPPORTUNITY["external_id"]]
        if not existing:
            store.close()
            return {"ok": False, "steps": steps, "error": r.error or "import failed"}
        oid = existing[0].id
    else:
        oid = r.output["imported_ids"][0]

    # Duplicate import must be idempotent
    r2 = coord.run_agent("opportunity_researcher", opportunities=[SAMPLE_OPPORTUNITY])
    steps.append({"step": "import_dedupe", "ok": r2.ok, "count": r2.output.get("count", 0)})

    # 2. Qualify
    r = coord.run_agent("qualification", opportunity_id=oid)
    steps.append({"step": "qualify", **_sum(r)})
    if not r.output.get("qualified"):
        store.close()
        return {"ok": False, "steps": steps, "error": "not qualified", "metrics": pipeline_metrics(store)}

    # 3. Proposal + approval request
    r = coord.run_agent("proposal", opportunity_id=oid)
    steps.append({"step": "proposal", **_sum(r)})
    approval_id = r.output.get("approval_id")

    # 4. Owner approves
    apr = coord.process_approval(approval_id, approved=True, reason="vertical slice approval")
    steps.append({"step": "approve", "ok": apr["status"] == "approved", "approval_id": approval_id})

    # 5. Mark won (simulates client acceptance after manual submit)
    won = coord.mark_won(oid)
    steps.append({"step": "won", "ok": won["status"] == "won", "status": won["status"]})

    # 6. Plan delivery
    r = coord.run_agent("delivery_planner", opportunity_id=oid)
    steps.append({"step": "plan", **_sum(r)})
    project_id = r.output.get("project_id")

    # 7. Build demo deliverable
    r = coord.run_agent("engineering", project_id=project_id)
    steps.append({"step": "build", **_sum(r)})
    preview = r.output.get("preview")

    # 8. Independent review
    r = coord.run_agent("independent_reviewer", project_id=project_id)
    steps.append({"step": "review", **_sum(r)})

    # 9. Invoice
    r = coord.run_agent("finance", project_id=project_id, provider=provider, customer_email="buyer@acme.example")
    steps.append({"step": "invoice", **_sum(r)})
    invoice_id = r.output.get("invoice_id")
    provider_ref = r.output.get("provider_ref")

    settled = False
    if mark_paid and provider_ref:
        # Explicit sandbox mark — labeled simulated.
        provider.mark_paid(provider_ref)
        from .agents import FinanceAgent
        fin = FinanceAgent(store, coord.auth, workdir=workdir)
        settle = fin.check_settlement(invoice_id, provider)
        # Idempotent second settle
        settle2 = fin.check_settlement(invoice_id, provider)
        settled = bool(settle.output.get("settled") or settle2.output.get("already_recorded"))
        steps.append({
            "step": "settle",
            "ok": settle.ok,
            "settled": settle.output.get("settled"),
            "idempotent_replay": settle2.output.get("already_recorded"),
            "simulated": True,
            "amount_cents": settle.output.get("amount_cents"),
        })

    # 10. Hourly review
    review = run_hourly_review(coord)
    steps.append({"step": "hourly_review", "ok": True, "bottleneck": review["bottleneck"]})

    metrics = pipeline_metrics(store)
    summary = {
        "ok": all(s.get("ok", True) for s in steps),
        "opportunity_id": oid,
        "project_id": project_id,
        "preview": preview,
        "invoice_id": invoice_id,
        "settled_simulated": settled,
        "steps": steps,
        "metrics": metrics,
        "latest_review": store.latest_review(),
        "integrations": integration_status(),
    }
    (workdir / "vertical_slice_report.json").write_text(json.dumps(summary, indent=2, default=str))
    store.close()
    return summary


def integration_status() -> dict:
    import os
    return {
        "anthropic": "live" if os.environ.get("ANTHROPIC_API_KEY") else "missing — agents use deterministic paths where possible",
        "stripe": "live" if os.environ.get("STRIPE_API_KEY") and os.environ.get("EARNER_MODE") == "live" else "sandbox mock",
        "upwork": "live" if os.environ.get("UPWORK_CLIENT_ID") else "mocked / disabled until OAuth",
        "freelancer_rss": "live read-only (watch)",
        "proposal_submit": "manual handoff only — no official submit API",
        "gmail": "live" if os.environ.get("GMAIL_USER") else "not configured",
        "payout_config": "encrypted local store — configure via dashboard",
    }


def _sum(r) -> dict:
    return {"ok": r.ok, "error": r.error, "output_keys": list((r.output or {}).keys())}


def load_sample_file(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return data
    if "opportunities" in data:
        return data["opportunities"]
    # Adapt seeds/leads-freelancer format
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

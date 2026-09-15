"""Three offers we can actually deliver — no invented capabilities."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Offer:
    id: str
    name: str
    buyer: str
    problem: str
    deliverables: tuple[str, ...]
    exclusions: tuple[str, ...]
    acceptance: tuple[str, ...]
    price_cents_low: int
    price_cents_high: int
    duration_days: int
    capacity_slots: int  # concurrent projects this offer can support
    expected_cost_cents: int
    proof: str


# Verified delivery set only. No model training, enterprise security, etc.
OFFERS: tuple[Offer, ...] = (
    Offer(
        id="landing_page",
        name="Conversion landing page",
        buyer="SMB / mid-market marketing owner needing a focused campaign page",
        problem="No clear page that states the offer and captures leads",
        deliverables=(
            "Single responsive landing page",
            "Copy aligned to agreed brief",
            "Lead CTA / form placeholder wired to owner endpoint if provided",
            "Preview URL + source handoff",
        ),
        exclusions=("SEO campaign management", "Paid ads", "Full brand identity system"),
        acceptance=(
            "Page loads on mobile and desktop",
            "Brand/headline/CTA match agreed brief",
            "Owner accepts preview in writing",
        ),
        price_cents_low=150_000,
        price_cents_high=500_000,
        duration_days=7,
        capacity_slots=2,
        expected_cost_cents=40_000,
        proof="Delivered via isolated workspace + independent review before handoff",
    ),
    Offer(
        id="workflow_automation",
        name="Approved workflow automation",
        buyer="Ops lead with a repetitive approved process (email→CRM, triage, notify)",
        problem="Manual handoffs waste hours and drop follow-ups",
        deliverables=(
            "Documented automation with named triggers/outputs",
            "Implementation in agreed tool (n8n/Zapier/custom) under owner credentials",
            "Audit log of runs",
        ),
        exclusions=("CAPTCHA/identity bypass", "Scraping behind login walls", "Account rental"),
        acceptance=(
            "Each step has trigger + output",
            "Test run succeeds on sample payload",
            "Owner credentials never stored in repo",
        ),
        price_cents_low=200_000,
        price_cents_high=800_000,
        duration_days=10,
        capacity_slots=1,
        expected_cost_cents=60_000,
        proof="Requires owner-granted tool access; refused without it",
    ),
    Offer(
        id="software_fix",
        name="Narrowly scoped software fix",
        buyer="Technical owner with a bounded bug or patch in an existing codebase",
        problem="A specific defect or small feature blocks shipping",
        deliverables=(
            "Patch in isolated workspace",
            "Regression tests appropriate to the change",
            "Diff + notes for review",
        ),
        exclusions=("Greenfield apps", "Infrastructure/ops ownership", "Security audits as a product"),
        acceptance=(
            "Reproduced issue addressed",
            "Tests pass for the change",
            "Independent review signed off",
        ),
        price_cents_low=100_000,
        price_cents_high=400_000,
        duration_days=5,
        capacity_slots=2,
        expected_cost_cents=30_000,
        proof="Only accepted when scope fits a single clear defect/patch",
    ),
)


def offer_by_id(oid: str) -> Offer | None:
    for o in OFFERS:
        if o.id == oid:
            return o
    return None


def capacity_snapshot(active_delivery_by_offer: dict[str, int]) -> dict:
    """Compare committed delivery slots vs catalog capacity."""
    rows = []
    total_slots = 0
    total_used = 0
    for o in OFFERS:
        used = int(active_delivery_by_offer.get(o.id, 0))
        free = max(0, o.capacity_slots - used)
        total_slots += o.capacity_slots
        total_used += used
        rows.append({
            "offer_id": o.id,
            "name": o.name,
            "slots": o.capacity_slots,
            "used": used,
            "free": free,
            "price_usd": f"${o.price_cents_low/100:,.0f}–${o.price_cents_high/100:,.0f}",
            "duration_days": o.duration_days,
        })
    return {
        "offers": rows,
        "total_slots": total_slots,
        "committed": total_used,
        "available": max(0, total_slots - total_used),
        "note": "Capacity is concurrent project slots for verified offers only.",
    }

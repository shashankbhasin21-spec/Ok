"""Commercial forecast — scenarios only when conversion history is insufficient."""

from __future__ import annotations

from datetime import date, datetime, timezone

from .commercial import BOOKINGS_DEADLINE, BOOKINGS_TARGET_CENTS, ILLUSTRATIVE_SCENARIOS
from .store import FirmStore


def days_remaining(today: date | None = None) -> int:
    today = today or datetime.now(timezone.utc).date()
    return max(0, (BOOKINGS_DEADLINE - today).days)


def build_forecast(store: FirmStore, today: date | None = None) -> dict:
    """Truthful forecast. Labels assumptions; does not invent close rates as measured fact."""
    snap = store.commercial_snapshot()
    days = days_remaining(today)
    remaining_bookings = max(0, BOOKINGS_TARGET_CENTS - snap["signed_bookings_cents"])

    # Observed conversions — only if denominators exist
    opps = [o for o in store.list_opportunities() if not o.simulated]
    submitted = sum(1 for o in opps if o.status in (
        "submitted", "replied", "won", "delivery", "review", "accepted", "invoiced", "paid", "lost"
    ))
    replied = sum(1 for o in opps if o.status in (
        "replied", "won", "delivery", "review", "accepted", "invoiced", "paid", "lost"
    ))
    signed = snap["signed_bookings_count"]

    measured = {}
    assumptions = []
    if submitted >= 5 and replied >= 0:
        measured["submit_to_reply"] = replied / submitted if submitted else None
    else:
        assumptions.append("submit→reply rate not measured (need ≥5 submitted proposals with outcomes)")
    if replied >= 5:
        measured["reply_to_sign"] = signed / replied if replied else None
    else:
        assumptions.append("reply→sign rate not measured (need ≥5 buyer replies with outcomes)")

    # Illustrative scenarios — explicitly not forecasts
    illustrative = []
    for sc in ILLUSTRATIVE_SCENARIOS:
        illustrative.append({
            **sc,
            "total_cents": sc["contracts"] * sc["avg_cents"],
            "note": "planning scenario only — not evidence that this demand exists",
        })

    # Required proposal value if we *assumed* 20% close — labeled assumption
    assumed_close = 0.20
    required_proposal_at_20pct = int(remaining_bookings / assumed_close) if assumed_close else None

    plausibly_closable = 0  # without signed evidence and short cycle, do not invent
    if days < 1:
        feasibility = "deadline passed or today is deadline"
    elif snap["signed_bookings_cents"] >= BOOKINGS_TARGET_CENTS:
        feasibility = "bookings target met"
    elif days <= 15 and snap["signed_bookings_cents"] == 0 and snap["proposals_delivered_cents"] == 0:
        feasibility = (
            "INFEASIBLE as a guaranteed outcome: 0 signed bookings, 0 externally delivered "
            f"proposals, {days} days left, and no measured close rate. Large cold enterprise "
            "deals typically exceed this window."
        )
    elif days <= 15 and snap["signed_bookings_cents"] == 0:
        feasibility = (
            "HIGH RISK / likely infeasible at $300k: no signed bookings yet; "
            "marketplace first contracts are typically $100–$500, not $25k–$100k."
        )
    else:
        feasibility = "insufficient measured conversion history to claim a path to target"

    return {
        "bookings_target_cents": BOOKINGS_TARGET_CENTS,
        "bookings_deadline": BOOKINGS_DEADLINE.isoformat(),
        "days_remaining": days,
        "remaining_bookings_cents": remaining_bookings,
        "snapshot": snap,
        "measured_conversion": measured,
        "assumptions": assumptions,
        "illustrative_scenarios": illustrative,
        "assumed_20pct_close_required_proposal_cents": required_proposal_at_20pct,
        "assumed_20pct_note": (
            "If (and only if) a 20% close-by-proposal-value rate held, "
            f"${(required_proposal_at_20pct or 0)/100:,.0f} in proposed value would be needed "
            "for the remaining bookings gap. This is an illustration, not a measured rate."
        ),
        "deals_plausibly_closable_before_deadline_cents": plausibly_closable,
        "feasibility": feasibility,
        "main_blocker": _blocker(snap, days),
        "next_actions": _next_actions(snap, days),
    }


def _blocker(snap: dict, days: int) -> str:
    if snap["cash_collected_cents"] == 0 and snap["signed_bookings_cents"] == 0:
        if snap["proposals_delivered_cents"] == 0:
            return "no_external_proposal_submissions_with_receipts"
        return "no_signed_bookings_yet"
    if snap["signed_bookings_cents"] > 0 and snap["cash_collected_cents"] == 0:
        return "payment_settlement_or_invoicing"
    return "scale_qualified_demand"


def _next_actions(snap: dict, days: int) -> list[str]:
    actions = []
    if snap["proposals_delivered_cents"] == 0:
        actions.append(
            "Approve drafted proposals and paste them on Freelancer/Upwork; "
            "record board_submission_receipt evidence (internal approval ≠ submitted)."
        )
    actions.append(
        "Open Payoneer/Wise for India payouts (Kotak often cannot link to US Stripe); "
        "set marketplace withdrawal method."
    )
    if days <= 20:
        actions.append(
            "Re-scope near-term offer to $100–$500 fixed scopes for first review-producing "
            "contracts; do not forecast $25k–$100k enterprise closes inside this window without evidence."
        )
    return actions[:3]

"""Revenue targets, and honest arithmetic about whether you'll hit them.

A target is a number and a deadline. Everything here is derived from the
ledger, so the status can only improve when real money settles — you cannot
talk this module into saying you're on track.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class Target:
    amount_cents: int
    hours: float
    started_at: float

    @property
    def deadline(self) -> float:
        return self.started_at + self.hours * 3600

    @property
    def hours_elapsed(self) -> float:
        return max(0.0, (time.time() - self.started_at) / 3600)

    @property
    def hours_left(self) -> float:
        return max(0.0, (self.deadline - time.time()) / 3600)

    @property
    def expired(self) -> bool:
        return time.time() >= self.deadline


@dataclass
class Status:
    target_cents: int
    settled_cents: int
    outstanding_cents: int
    cost_cents: int
    hours_elapsed: float
    hours_left: float
    on_track: bool
    gap_cents: int
    required_rate_cents_per_hour: float
    actual_rate_cents_per_hour: float
    verdict: str

    def brief(self) -> str:
        return (
            f"Target ${self.target_cents / 100:,.0f} · "
            f"settled ${self.settled_cents / 100:,.2f} · "
            f"invoiced-unpaid ${self.outstanding_cents / 100:,.2f} · "
            f"spend ${self.cost_cents / 100:,.2f} · "
            f"{self.hours_left:.1f}h left → {self.verdict}"
        )


def load(path: Path) -> Target | None:
    if not path.exists():
        return None
    return Target(**json.loads(path.read_text()))


def save(path: Path, target: Target) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(target), indent=2))


def evaluate(target: Target, ledger) -> Status:
    settled = ledger.revenue_cents()
    outstanding = ledger.outstanding_cents()
    cost = ledger.cost_cents()
    gap = max(0, target.amount_cents - settled)

    hours_left = target.hours_left
    required = gap / hours_left if hours_left > 0 else float("inf")
    actual = settled / target.hours_elapsed if target.hours_elapsed > 0.01 else 0.0

    if settled >= target.amount_cents:
        verdict = "HIT — target met in settled cash"
    elif hours_left <= 0:
        verdict = f"MISSED — ${gap / 100:,.2f} short at the deadline"
    elif settled + outstanding >= target.amount_cents:
        verdict = (
            f"AT RISK — enough is invoiced to cover the gap, but "
            f"${outstanding / 100:,.2f} of it is unpaid. Chase collections."
        )
    elif actual >= required:
        verdict = "ON TRACK — current settlement rate clears the gap"
    else:
        need = (target.amount_cents - settled - outstanding) / 100
        verdict = (
            f"BEHIND — need ${required / 100:,.2f}/h, running at "
            f"${actual / 100:,.2f}/h. ${need:,.2f} still has to be sold, not just delivered."
        )

    return Status(
        target_cents=target.amount_cents,
        settled_cents=settled,
        outstanding_cents=outstanding,
        cost_cents=cost,
        hours_elapsed=target.hours_elapsed,
        hours_left=hours_left,
        on_track=settled >= target.amount_cents or actual >= required,
        gap_cents=gap,
        required_rate_cents_per_hour=required,
        actual_rate_cents_per_hour=actual,
        verdict=verdict,
    )


def plan_for(status: Status, average_deal_cents: int) -> dict:
    """What the gap means in units of work, not vibes."""
    avg = max(1, average_deal_cents)
    deals_needed = -(-status.gap_cents // avg)  # ceil
    per_hour = deals_needed / status.hours_left if status.hours_left > 0 else float("inf")
    return {
        "gap_cents": status.gap_cents,
        "average_deal_cents": avg,
        "deals_needed": deals_needed,
        "deals_per_hour": round(per_hour, 2),
        "leads_needed_at_30pct_close": deals_needed * 3,
    }

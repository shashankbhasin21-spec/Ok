"""Quantum Brain CEO — Anestasis Grey.

Has operational command inside the commerce module: run teams, issue directives,
prioritize publish/promote cycles. Cannot invent settled cash, cannot spend ad
budget without owner caps, cannot write bank credentials.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import (
    BILLION_REVENUE_VISION_CENTS,
    CEO_NAME,
    CEO_TITLE,
    COMPANY_NAME,
    DAILY_SALES_TARGET_CENTS,
    MONTHLY_SALES_TARGET_CENTS,
    PAYMENT_NOTE,
)
from .store import CommerceStore
from .teams import (
    ensure_teams,
    run_daily_promotion,
    run_discussion_room,
    run_loophole_squad,
    run_outreach_optional,
    run_production_finder,
    run_webpage_developer,
)


class QuantumBrainCEO:
    name = CEO_NAME
    title = CEO_TITLE
    company = COMPANY_NAME

    def __init__(self, store: CommerceStore, workdir: Path):
        self.store = store
        self.workdir = workdir
        ensure_teams(store)
        store.set_meta("ceo", CEO_NAME)
        store.set_meta("company", COMPANY_NAME)

    def status(self) -> dict:
        settled = self.store.settled_sales_cents()
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        month_settled = self.store.month_settled_sales_cents(month)
        products = self.store.list_products()
        published = [p for p in products if p["status"] == "published"]
        return {
            "ceo": self.name,
            "title": self.title,
            "company": self.company,
            "command": "full_commerce_ops",
            "targets": {
                "daily_sales_usd": DAILY_SALES_TARGET_CENTS / 100,
                "monthly_sales_usd": MONTHLY_SALES_TARGET_CENTS / 100,
                "billion_vision_usd": BILLION_REVENUE_VISION_CENTS / 100,
                "label": "Targets only — not guarantees or spend authorization.",
            },
            "settled_sales_usd": settled / 100,
            "month_settled_sales_usd": month_settled / 100,
            "month_utc": month,
            "daily_progress": settled / DAILY_SALES_TARGET_CENTS if DAILY_SALES_TARGET_CENTS else 0,
            "monthly_progress": month_settled / MONTHLY_SALES_TARGET_CENTS if MONTHLY_SALES_TARGET_CENTS else 0,
            "catalog": {
                "total": len(products),
                "published": len(published),
                "draft": len([p for p in products if p["status"] == "draft"]),
                "compliance_hold": len([p for p in products if p["status"] == "compliance_hold"]),
            },
            "open_directives": [d for d in self.store.list_directives() if d["status"] == "open"],
            "teams": self.store.list_teams(),
            "payment_note": PAYMENT_NOTE,
            "blocker": self._blocker(published, settled),
        }

    def _blocker(self, published: list, settled: int) -> str:
        if not published:
            return "No published products — run webpage developer with publish=true after sourcing check."
        if settled <= 0:
            return (
                "Storefront ready for unpaid reservations; settled revenue needs live "
                "Stripe/Razorpay + provider payment confirmation. Indian bank details later."
            )
        return "Revenue loop partially live — scale ads only inside owner spend caps."

    def direct(self, directive: str, priority: str = "high") -> dict:
        return self.store.add_directive(directive, priority=priority)

    def run_company_day(self, *, publish: bool = False, outreach: bool = True) -> dict:
        """One operating cycle under CEO command."""
        self.direct(
            "Execute daily commerce cycle: find loops, draft pages, draft ads, log discussion ideas.",
            priority="high",
        )
        results = {
            "ceo": self.name,
            "production_finder": run_production_finder(self.store),
            "loophole_squad": run_loophole_squad(self.store, self.workdir),
            "webpage_developer": run_webpage_developer(self.store, self.workdir, publish=publish),
            "daily_promotion": run_daily_promotion(self.store),
            "discussion_room": run_discussion_room(self.store),
        }
        if outreach:
            results["outreach"] = run_outreach_optional(self.store)
        import json
        import time as _time

        ceo_team = next(t for t in self.store.list_teams() if t["code"] == "F_quantum_ceo")
        self.store.upsert_team(
            code="F_quantum_ceo",
            name=ceo_team["name"],
            mission=ceo_team["mission"],
            headcount=ceo_team["headcount"],
            status="commanded",
            last_run_at=_time.time(),
            last_output_json=json.dumps({"cycle": "run_company_day", "publish": publish}),
        )
        results["status"] = self.status()
        return results

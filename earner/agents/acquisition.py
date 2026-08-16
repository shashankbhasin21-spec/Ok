"""Acquisition — turns raw leads into work the delivery agent can bill.

It does not spam anyone. It reads leads you actually have (a form, a
marketplace export, a referral list), researches each one for real, and writes
a specific proposal that a human approves before it goes out. A reply lands
back in ``inbox/requests`` and becomes a paid job.
"""

from __future__ import annotations

import json

from ..agent import BaseAgent, TickReport
from ..approval import Action

FIT_SCHEMA = {
    "type": "object",
    "properties": {
        "worth_pursuing": {"type": "boolean"},
        "reason": {"type": "string"},
        "offer": {"type": "string"},
        "price_cents": {"type": "integer"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["worth_pursuing", "reason", "offer", "price_cents", "confidence"],
    "additionalProperties": False,
}


class AcquisitionAgent(BaseAgent):
    name = "acquisition"
    role = "Acquisition"
    description = "Researches real leads and drafts specific, approvable proposals."

    system_prompt = (
        "You win professional-services work. You research a prospect properly before writing to "
        "them, and your proposals name the specific problem you noticed and what you will deliver "
        "about it. You never claim a relationship, a credential, or a result you do not have. "
        "A vague or template-shaped proposal is worse than no proposal, so you decline leads you "
        "cannot say something specific about."
    )

    def tick(self) -> TickReport:
        report = TickReport(agent=self.name)
        for ref, lead in self.read_inbox("leads"):
            opp_id = self.ledger.record_opportunity(self.name, "inbox/leads", ref, lead)
            if opp_id is None:
                continue
            report.found += 1
            try:
                self._pursue(ref, lead, opp_id, report)
            except Exception as exc:  # noqa: BLE001 - one bad lead must not stop the run
                report.errors.append(f"lead {ref}: {type(exc).__name__}: {exc}")
                self.ledger.set_opportunity_status(opp_id, "failed")
        return report

    def _pursue(self, ref: str, lead: dict, opp_id: int, report: TickReport) -> None:
        company = lead.get("company") or lead.get("name") or ref
        research = self.think(
            None,
            "Research this prospect and find one concrete, checkable thing about their current "
            "situation that a professional-services engagement could improve.\n\n"
            f"Company: {company}\n"
            f"Website: {lead.get('website', 'unknown')}\n"
            f"Context we were given: {json.dumps(lead)}\n\n"
            "Use search. Report only what you can source, and say plainly when you found nothing "
            "specific — a blank is a useful answer here and stops us sending a generic pitch.",
            research=True,
            effort="medium",
            max_tokens=4000,
        )
        research_cost = self.last_cost_cents

        raw = self.think(
            None,
            "Decide whether to pursue this lead and, if so, draft the offer.\n\n"
            f"Prospect: {company}\n"
            f"Service we sell: {lead.get('service') or self.cfg.offer}\n\n"
            f"Research findings:\n{research}\n\n"
            "Set worth_pursuing=false if the research turned up nothing specific enough to write a "
            "non-generic proposal. Price in US cents at what the outcome is worth to them.",
            schema=FIT_SCHEMA,
            effort="medium",
        )
        fit = json.loads(raw)
        total_cost = research_cost + self.last_cost_cents

        self.ledger.log(
            "opportunity", opp_id, "researched", company=company, cost_cents=total_cost, fit=fit
        )

        if not fit["worth_pursuing"]:
            self.ledger.set_opportunity_status(opp_id, "declined")
            report.rejected += 1
            return

        proposal = self.think(
            None,
            "Write the outreach email.\n\n"
            f"Prospect: {company}\n"
            f"Contact: {lead.get('contact_name', 'there')}\n"
            f"What we found: {research}\n"
            f"Offer: {fit['offer']}\n"
            f"Price: ${fit['price_cents'] / 100:,.2f}\n\n"
            "Short. Open with the specific thing we noticed, not with who we are. One clear offer, "
            "one clear price, one question at the end. No flattery, no 'I hope this finds you "
            "well', no invented familiarity.",
            effort="medium",
            max_tokens=1500,
        )

        decision = self.gate.review(
            Action(
                kind="message",
                summary=f"outreach to {company} ({fit['confidence']} confidence)",
                body=proposal,
                amount_cents=fit["price_cents"],
                recipient=lead.get("email"),
            )
        )
        if not decision.ok:
            self.ledger.set_opportunity_status(opp_id, "held")
            report.held += 1
            return

        self.write_outbox(f"outreach-{ref}.txt", f"To: {lead.get('email')}\n\n{proposal}")
        self.ledger.set_opportunity_status(opp_id, "contacted")
        report.quoted += 1

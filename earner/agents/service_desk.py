"""Delivery — the agent that does paid client work.

Reads real inbound requests, prices them, invoices, and once the money has
actually settled, produces the deliverable. Prepay by default: no work happens
on spec, so an unpaid client can never consume tokens.
"""

from __future__ import annotations

import json

from ..agent import Deliverable, EarningAgent, Opportunity, Quote, PREPAY
from ..ledger import Job

QUOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "accept": {"type": "boolean"},
        "reason": {"type": "string"},
        "price_cents": {"type": "integer"},
        "scope": {"type": "string"},
        "estimated_output_words": {"type": "integer"},
    },
    "required": ["accept", "reason", "price_cents", "scope", "estimated_output_words"],
    "additionalProperties": False,
}


class ServiceDeskAgent(EarningAgent):
    name = "delivery"
    role = "Delivery lead"
    description = "Takes paid client requests end to end: scope, quote, invoice, produce, deliver."
    payment_timing = PREPAY

    system_prompt = (
        "You are the delivery lead of a small professional services firm. You scope, price, and "
        "produce written client deliverables: research briefs, analyses, documentation, copy, "
        "technical write-ups. You are accountable for the work being correct and useful, not for "
        "it being long. Decline work you cannot do well from the brief alone."
    )

    # Guardrails so a bad brief or an odd model answer can't produce an absurd price.
    MIN_PRICE_CENTS = 5_000
    MAX_PRICE_CENTS = 250_000

    def find_opportunities(self) -> list[Opportunity]:
        opportunities = []
        for ref, data in self.read_inbox("requests"):
            email = data.get("email")
            brief = data.get("brief")
            if not email or not brief:
                continue  # unusable lead; a human can fix the file and it'll be picked up next tick
            opportunities.append(
                Opportunity(
                    external_ref=ref,
                    source="inbox/requests",
                    title=data.get("title") or brief[:60],
                    customer_email=email,
                    payload=data,
                )
            )
        return opportunities

    def qualify(self, opp: Opportunity) -> Quote | None:
        budget = opp.payload.get("budget_cents")
        prompt = (
            "Scope and price this client request.\n\n"
            f"Client: {opp.customer_email}\n"
            f"Title: {opp.title}\n"
            f"Brief:\n{opp.payload.get('brief')}\n\n"
            f"Stated budget (cents): {budget if budget else 'not given'}\n\n"
            "Decline (accept=false) if the brief is too vague to deliver against, requires access "
            "to systems or data you were not given, or is work you cannot do well in writing. "
            "Otherwise price it at what the work is worth to this client, in US cents, between "
            f"{self.MIN_PRICE_CENTS} and {self.MAX_PRICE_CENTS}. Write the scope as a short "
            "statement of exactly what will be delivered — it goes on the invoice."
        )
        raw = self.think(None, prompt, schema=QUOTE_SCHEMA, effort="medium")
        decision = json.loads(raw)
        qualification_cost = self.last_cost_cents

        if not decision["accept"]:
            self.ledger.log("opportunity", opp.id, "declined", reason=decision["reason"])
            return None

        price = max(self.MIN_PRICE_CENTS, min(self.MAX_PRICE_CENTS, int(decision["price_cents"])))
        if budget:
            price = min(price, int(budget))

        # Rough forward estimate of what producing this will cost in tokens, so
        # the pipeline's margin check has something real to test against.
        words = max(300, int(decision["estimated_output_words"]))
        estimated_cost = qualification_cost + self._estimate_production_cents(words)

        return Quote(
            amount_cents=price,
            scope=decision["scope"],
            estimated_cost_cents=estimated_cost,
            currency=self.cfg.currency,
            qualification_cost_cents=qualification_cost,
        )

    def _estimate_production_cents(self, words: int) -> int:
        from ..llm import cost_cents

        output_tokens = int(words * 1.4)
        # Thinking plus a couple of revision passes; deliberately pessimistic.
        return cost_cents(self.cfg.model, 4_000, output_tokens * 3)

    def produce(self, job: Job, payload: dict) -> Deliverable:
        prompt = (
            "Produce the paid deliverable. The client has already paid, so deliver the finished "
            "work — not a plan for it, not an outline.\n\n"
            f"Agreed scope: {job.notes}\n\n"
            f"Original brief:\n{payload.get('brief')}\n\n"
            f"Extra context: {json.dumps(payload.get('context', {}))}\n\n"
            "Write it in Markdown. Lead with the answer or the finding — the client reads the top "
            "first. State assumptions you had to make in a short section at the end. Where you are "
            "uncertain or the brief was ambiguous, say so plainly rather than papering over it."
        )
        content = self.think(job, prompt, effort="high", max_tokens=self.cfg.max_tokens)
        return Deliverable(
            filename=f"{_slug(job.title)}.md",
            content=content,
            summary=f"{job.title} — {len(content.split())} words against scope: {job.notes}",
        )

    def deliver(self, job: Job, deliverable: Deliverable, payload: dict) -> str:
        path = super().deliver(job, deliverable, payload)
        note = (
            f"To: {payload.get('email')}\n"
            f"Subject: Delivered — {job.title}\n\n"
            f"Hi,\n\nYour {job.title} is attached ({path}).\n\n"
            f"Scope delivered: {job.notes}\n\n"
            "If anything is off against that scope, reply and we'll fix it.\n"
        )
        self.write_outbox(f"job-{job.id}-delivery.txt", note)
        return path


def _slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    return "".join(keep).strip("-")[:50] or "deliverable"

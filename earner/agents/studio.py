"""Product — builds digital products once and sells them repeatedly.

Service work trades hours for money; this role builds an asset that can be sold
more than once. It writes the product, prices it, and publishes a listing with
a real payment link. Revenue appears only when a real buyer actually pays —
nothing here simulates a sale.
"""

from __future__ import annotations

import json

from ..agent import Deliverable, EarningAgent, Opportunity, Quote, POSTPAY
from ..ledger import Job

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "viable": {"type": "boolean"},
        "reason": {"type": "string"},
        "product_title": {"type": "string"},
        "audience": {"type": "string"},
        "price_cents": {"type": "integer"},
        "outline": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["viable", "reason", "product_title", "audience", "price_cents", "outline"],
    "additionalProperties": False,
}


class ContentStudioAgent(EarningAgent):
    name = "product"
    role = "Product builder"
    description = "Builds sellable digital products from a brief and publishes a paid listing."
    payment_timing = POSTPAY  # the product is built first, then listed for sale

    system_prompt = (
        "You build small, genuinely useful digital products — guides, templates, playbooks, "
        "reference kits — for a specific paying audience. You have opinions about what is worth "
        "buying: a product that restates what a buyer could get free in ten minutes is not viable, "
        "and you say so rather than building it."
    )

    MIN_PRICE_CENTS = 900
    MAX_PRICE_CENTS = 29_900

    # How many self-sourced topics to research when nobody has queued a brief.
    SCOUT_BATCH = 3

    def find_opportunities(self) -> list[Opportunity]:
        briefs = self.read_inbox("products")
        if not briefs and self.cfg.autonomous:
            briefs = self._scout_niches()

        opportunities = []
        for ref, data in briefs:
            topic = data.get("topic")
            if not topic:
                continue
            opportunities.append(
                Opportunity(
                    external_ref=ref,
                    source="inbox/products",
                    title=data.get("title") or topic[:60],
                    customer_email=data.get("storefront_email", ""),
                    payload=data,
                )
            )
        return opportunities

    def _scout_niches(self) -> list[tuple[str, dict]]:
        """Find its own topics when the brief queue is empty.

        Uses live search rather than model memory: what people are paying for
        changes faster than any training cutoff, and a product aimed at last
        year's demand is a product nobody buys.
        """
        import hashlib
        import json as _json

        raw = self.think(
            None,
            "Find product topics worth building right now.\n\n"
            "Search for what a specific professional audience is currently struggling with and "
            f"already spending money to solve. Return exactly {self.SCOUT_BATCH} topics as a JSON "
            'array of objects with keys "topic", "audience", "notes", "evidence".\n\n'
            "Rules: the audience must be someone with a budget, not consumers in general. "
            "'evidence' must cite what you actually found — a paid tool, a course, a job posting, "
            "a forum thread — not a guess about demand. Reject anything already saturated with "
            "free, good material. Return only the JSON array.",
            research=True,
            effort="medium",
            max_tokens=6000,
        )
        cost = self.last_cost_cents
        text = raw.strip()
        if "[" in text:  # trim any prose around the array
            text = text[text.index("[") : text.rindex("]") + 1]
        try:
            topics = _json.loads(text)
        except _json.JSONDecodeError:
            self.ledger.log("agent", None, "scout_failed", cost_cents=cost, sample=raw[:300])
            return []

        self.ledger.log("agent", None, "scouted_niches", count=len(topics), cost_cents=cost)
        out = []
        for topic in topics[: self.SCOUT_BATCH]:
            if not isinstance(topic, dict) or not topic.get("topic"):
                continue
            ref = "scout-" + hashlib.sha256(topic["topic"].encode()).hexdigest()[:12]
            out.append((ref, topic))
        return out

    def qualify(self, opp: Opportunity) -> Quote | None:
        prompt = (
            "Assess this product idea and plan it.\n\n"
            f"Topic: {opp.payload.get('topic')}\n"
            f"Intended audience: {opp.payload.get('audience', 'not specified')}\n"
            f"Notes: {opp.payload.get('notes', '')}\n\n"
            "Set viable=false if this cannot be a product someone would pay for — say why in one "
            "sentence. If viable, price it in US cents between "
            f"{self.MIN_PRICE_CENTS} and {self.MAX_PRICE_CENTS} based on what it saves the buyer, "
            "and give an outline of 4-8 sections."
        )
        raw = self.think(None, prompt, schema=PLAN_SCHEMA, effort="medium")
        plan = json.loads(raw)
        qualification_cost = self.last_cost_cents

        if not plan["viable"]:
            self.ledger.log("opportunity", opp.id, "declined", reason=plan["reason"])
            return None

        price = max(self.MIN_PRICE_CENTS, min(self.MAX_PRICE_CENTS, int(plan["price_cents"])))
        scope = json.dumps(
            {
                "product_title": plan["product_title"],
                "audience": plan["audience"],
                "outline": plan["outline"],
            }
        )
        from ..llm import cost_cents

        return Quote(
            amount_cents=price,
            scope=scope,
            # One build, sold many times — the estimate covers writing it once.
            estimated_cost_cents=qualification_cost + cost_cents(self.cfg.model, 6_000, 12_000),
            currency=self.cfg.currency,
            qualification_cost_cents=qualification_cost,
        )

    def produce(self, job: Job, payload: dict) -> Deliverable:
        plan = json.loads(job.notes or "{}")
        prompt = (
            "Write the full product. This is the thing a buyer pays for, so it must be complete "
            "and immediately usable — no placeholders, no 'in this section we will'.\n\n"
            f"Title: {plan.get('product_title')}\n"
            f"Audience: {plan.get('audience')}\n"
            f"Outline: {json.dumps(plan.get('outline', []))}\n"
            f"Source notes: {payload.get('notes', '')}\n\n"
            "Markdown. Concrete over general: real examples, real numbers, checklists the reader "
            "can act on today. If a section would only restate common knowledge, cut it."
        )
        content = self.think(job, prompt, effort="high", max_tokens=self.cfg.max_tokens)
        return Deliverable(
            filename=f"{_slug(plan.get('product_title', job.title))}.md",
            content=content,
            summary=f"{plan.get('product_title')} — {len(content.split())} words, "
            f"listed at ${job.quote_cents / 100:,.2f}",
        )

    def deliver(self, job: Job, deliverable: Deliverable, payload: dict) -> str:
        """Publish the listing. The payment link is what turns this into revenue."""
        path = super().deliver(job, deliverable, payload)
        plan = json.loads(job.notes or "{}")
        listing = (
            f"# {plan.get('product_title', job.title)}\n\n"
            f"**Price:** ${job.quote_cents / 100:,.2f}\n"
            f"**For:** {plan.get('audience', 'general')}\n\n"
            f"{deliverable.summary}\n\n"
            f"Product file: {path}\n\n"
            "Payment link is created on the next tick and recorded in the ledger; "
            "revenue lands only when a real buyer pays it.\n"
        )
        self.write_outbox(f"listing-job-{job.id}.md", listing)
        return path


def _slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    return "".join(keep).strip("-")[:50] or "product"

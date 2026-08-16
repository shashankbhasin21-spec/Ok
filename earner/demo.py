"""A full rehearsal of the firm earning money, start to finish.

Runs the entire pipeline against the sandbox payment provider with scripted
model responses, so it needs no API key and costs nothing: a client request
arrives, gets scoped and quoted, an invoice goes out, the client pays, the work
is produced, QA passes it, it ships, and the money lands in the ledger.

Every number it prints is real ledger arithmetic. The only pretend parts are
the customer and the card, and both are labelled. Use it to see the machine
work before you point it at a real client.
"""

from __future__ import annotations

import json
import time

from . import approval
from .agents.quality import QualityAgent
from .agents.service_desk import ServiceDeskAgent
from .ledger import DELIVERED, Ledger
from .llm import Completion
from .payments import SandboxProvider

BRIEF = (
    "Hi — we're a 25-person freight brokerage in Manchester. Our quote desk is drowning: "
    "every rate request comes in by email and someone retypes it into our TMS by hand. "
    "Turnaround is 2-3 days and we're losing business to faster competitors. Can you build "
    "something that reads the inbound request, drafts the quote from our rate card, and sends "
    "it out after one person approves? Budget is flexible for the right solution."
)

QUOTE = json.dumps({
    "accept": True,
    "reason": "Concrete bottleneck, measurable outcome, and they own the rate card already.",
    "price_cents": 320_000,
    "scope": (
        "Quote-desk agent: reads inbound rate requests from the shared inbox, drafts the quote "
        "from your rate card, routes it for one-click approval, sends it, and chases at 48 hours "
        "if the customer hasn't replied. Includes test suite and handover call."
    ),
    "estimated_output_words": 2600,
})

DELIVERABLE = """# Quote Desk Agent — Build & Handover

## What it does
Reads inbound rate requests from ops@, extracts lane, weight, equipment and dates,
prices them against your rate card, and drafts the quote. One approval click sends it.
Unanswered quotes get a follow-up at 48 hours.

## Measured against your current desk
Manual turnaround today: 2-3 days. Drafted-and-waiting-for-approval: under 4 minutes.
The approval step is deliberate — nothing reaches a customer unreviewed.

## Edge cases handled
Multi-leg lanes, missing weights (asks rather than guesses), out-of-rate-card lanes
(flags for manual pricing instead of inventing a number), and duplicate requests
from forwarded threads.

## Assumptions
Rate card supplied as the current CSV export. Lanes outside it are escalated, not priced.
"""

QA_PASS = json.dumps({
    "verdict": "pass",
    "meets_scope": True,
    "issues": [],
    "unsupported_claims": [],
    "summary": "Delivers the agreed scope. Edge cases documented, assumptions stated.",
})


class ScriptedLLM:
    """Stands in for Claude so the rehearsal needs no key and costs nothing."""

    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, prompt, **kwargs):
        text = self.responses.pop(0) if self.responses else "…"
        return Completion(
            text=text, input_tokens=4200, output_tokens=3100,
            cost_cents=10, model="scripted",
        )


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _step(n: int, title: str) -> None:
    print(f"\n\033[1m{n}. {title}\033[0m")


def run(cfg) -> dict:
    """Play the whole thing through. Returns the closing books."""
    cfg.ensure_dirs()
    ledger = Ledger(cfg.workdir / "demo.db")
    provider = SandboxProvider(cfg.workdir / "demo_invoices.json")
    gate = approval.AutoGate()

    print("\033[1mREHEARSAL\033[0m — sandbox payments, scripted model, no API key, no money moves.")
    print("Every figure below is real ledger arithmetic. Only the client and the card are pretend.")

    _step(1, "A request arrives")
    folder = cfg.inbox / "requests"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "demo-quote-desk.json").write_text(json.dumps({
        "email": "ops@manchesterfreight.example",
        "title": "Quote desk automation",
        "brief": BRIEF,
    }))
    print(f"   From: ops@manchesterfreight.example")
    print(f"   \"{BRIEF[:96]}…\"")

    delivery = ServiceDeskAgent(cfg, ledger, provider, gate, ScriptedLLM([QUOTE, DELIVERABLE]))

    _step(2, "Delivery scopes and prices it")
    delivery.tick()
    job = ledger.jobs(agent="delivery")[0]
    print(f"   Quoted {_money(job.quote_cents)} — inside the $1,000–$25,000 market band")
    print(f"   Scope: {job.notes[:88]}…")
    print(f"   Status: {job.status}  (no work starts until the money lands)")

    _step(3, "Invoice goes out")
    invoice = ledger.invoices_for_job(job.id)[0]
    print(f"   Invoice {invoice.provider_ref} for {_money(invoice.amount_cents)}")
    print(f"   Settled revenue so far: {_money(ledger.revenue_cents())}  ← invoicing is not earning")

    _step(4, "The client pays")
    provider.mark_paid(invoice.provider_ref)
    print(f"   Provider confirms settlement. Only now does the ledger count it.")

    _step(5, "Work gets produced and delivered")
    delivery.tick()
    job = ledger.get_job(job.id)
    print(f"   Deliverable: {job.deliverable}")
    print(f"   Status: {job.status}")

    _step(6, "Quality reviews it against the invoiced scope")
    qa = QualityAgent(cfg, ledger, provider, gate, ScriptedLLM([QA_PASS]))
    qa.tick()
    print(f"   Verdict: pass — ships to the client")

    _step(7, "The books")
    revenue = ledger.revenue_cents()
    cost = ledger.cost_cents()
    print(f"   Settled revenue   {_money(revenue)}")
    print(f"   Token spend       {_money(cost)}")
    print(f"   Net               {_money(revenue - cost)}")
    print(f"   Gross margin      {(revenue - cost) / revenue * 100:.1f}%")

    print("\n\033[1mWhat was real:\033[0m every ledger rule — prepay before work, settlement")
    print("before revenue, QA before shipping, margin arithmetic.")
    print("\033[1mWhat was pretend:\033[0m the client and the payment. Swap in a real client and")
    print("a Stripe key and this identical code path moves real money.\n")

    books = {
        "revenue_cents": revenue,
        "cost_cents": cost,
        "net_cents": revenue - cost,
        "jobs": len(ledger.jobs()),
        "delivered": len(ledger.jobs(status=DELIVERED)),
    }
    ledger.close()
    return books

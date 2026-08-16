"""Collections — recovers money that is already owed.

The cheapest revenue in the business: no selling, no delivery, just getting
paid for work already done. It escalates politely and stops the moment the
provider confirms the invoice settled.
"""

from __future__ import annotations

import time

from ..agent import BaseAgent, TickReport
from ..approval import Action

# Escalation ladder, in days overdue.
LADDER = [
    (3, "reminder", "friendly nudge, assume it slipped"),
    (10, "follow_up", "direct, restate the work delivered and the amount"),
    (21, "final_notice", "firm, state that work pauses and what happens next"),
]


class CollectionsAgent(BaseAgent):
    name = "collections"
    role = "Collections"
    description = "Chases unpaid invoices on an escalation ladder until they settle."

    system_prompt = (
        "You collect on overdue invoices for a small firm. You are courteous and extremely clear: "
        "what was delivered, what is owed, when it was due, and how to pay. You never threaten "
        "anything the firm would not actually do, never invent late fees that were not agreed, "
        "and never accuse — most late invoices are an oversight, and the ones that are not are "
        "handled by escalating facts, not tone."
    )

    def tick(self) -> TickReport:
        report = TickReport(agent=self.name)

        # Settlement first: an invoice that just landed must not get chased.
        for invoice in self.ledger.open_invoices():
            try:
                settlement = self.provider.fetch_settlement(invoice.provider_ref)
            except Exception as exc:  # noqa: BLE001 - provider blip, retry next tick
                report.errors.append(f"{invoice.provider_ref}: {exc}")
                continue
            if settlement and self.ledger.settle(
                invoice, settlement.event_id, settlement.amount_cents, settlement.currency
            ):
                report.settled_cents += settlement.amount_cents

        for invoice in self.ledger.open_invoices():
            job = self.ledger.get_job(invoice.job_id)
            age_days = self._age_days(invoice)
            stage = self._stage_for(age_days)
            if stage is None:
                continue
            if self._already_sent(invoice.id, stage):
                continue
            try:
                self._chase(invoice, job, stage, age_days, report)
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"invoice {invoice.provider_ref}: {type(exc).__name__}: {exc}")
        return report

    def _age_days(self, invoice) -> float:
        row = self.ledger.conn.execute(
            "SELECT created_at FROM invoices WHERE id=?", (invoice.id,)
        ).fetchone()
        return (time.time() - row["created_at"]) / 86400

    def _stage_for(self, age_days: float) -> str | None:
        stage = None
        for days, name, _ in LADDER:
            if age_days >= days:
                stage = name
        return stage

    def _already_sent(self, invoice_id: int, stage: str) -> bool:
        row = self.ledger.conn.execute(
            "SELECT 1 FROM events WHERE entity='invoice' AND entity_id=? AND kind=? LIMIT 1",
            (invoice_id, f"chase_{stage}"),
        ).fetchone()
        return row is not None

    def _chase(self, invoice, job, stage: str, age_days: float, report: TickReport) -> None:
        tone = next(t for _, name, t in LADDER if name == stage)
        payload = self.ledger.opportunity_payload(job.opportunity_id)
        message = self.think(
            None,
            "Write the collections email.\n\n"
            f"Stage: {stage} ({tone})\n"
            f"Client: {payload.get('email', 'the client')}\n"
            f"Work delivered: {job.title}\n"
            f"Scope agreed: {job.notes}\n"
            f"Amount owed: ${invoice.amount_cents / 100:,.2f} {invoice.currency.upper()}\n"
            f"Days since invoice: {age_days:.0f}\n"
            f"Payment link: {invoice.url}\n\n"
            "Short. Facts, amount, link, one clear ask. No guilt, no boilerplate.",
            effort="low",
            max_tokens=1200,
        )

        decision = self.gate.review(
            Action(
                kind="message",
                summary=f"collections {stage} on invoice {invoice.provider_ref} "
                f"({age_days:.0f}d overdue)",
                body=message,
                amount_cents=invoice.amount_cents,
                recipient=payload.get("email"),
            )
        )
        if not decision.ok:
            report.held += 1
            return

        self.write_outbox(f"collections-{invoice.id}-{stage}.txt", message)
        self.ledger.log("invoice", invoice.id, f"chase_{stage}", days=round(age_days))
        report.quoted += 1

"""The earning pipeline.

    opportunity → qualify (price it) → approve → invoice
                → payment confirmed → do the work → approve → deliver

Two things are deliberately not automated: the decision to send something to a
customer, and the decision that money arrived. The first belongs to a human,
the second to the payment provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from . import approval, ledger as ledger_mod
from .approval import Action
from .ledger import Job, Ledger
from .llm import LLM, Refused

PREPAY = "prepay"  # invoice first, work after the money lands
POSTPAY = "postpay"  # work first, invoice on delivery


class BudgetExceeded(RuntimeError):
    """The job would spend more on tokens than its guardrail allows."""


@dataclass
class Opportunity:
    external_ref: str  # stable id from the source; drives deduplication
    source: str
    title: str
    customer_email: str
    payload: dict = field(default_factory=dict)
    id: int | None = None


@dataclass
class Quote:
    amount_cents: int
    scope: str
    estimated_cost_cents: int = 0
    currency: str = "usd"
    # Tokens already spent deciding whether to take the job. Billed to the job
    # so that even declined-then-accepted work carries its true cost.
    qualification_cost_cents: int = 0


@dataclass
class Deliverable:
    filename: str
    content: str
    summary: str = ""


@dataclass
class TickReport:
    agent: str
    found: int = 0
    quoted: int = 0
    invoiced: int = 0
    delivered: int = 0
    settled_cents: int = 0
    held: int = 0
    rejected: int = 0
    errors: list[str] = field(default_factory=list)

    def line(self) -> str:
        return (
            f"{self.agent}: found={self.found} quoted={self.quoted} invoiced={self.invoiced} "
            f"delivered={self.delivered} settled=${self.settled_cents / 100:,.2f} "
            f"held={self.held} rejected={self.rejected} errors={len(self.errors)}"
        )


class BaseAgent(ABC):
    """Shared staff behaviour: identity, a Claude budget, and an approval gate."""

    name: str = "agent"
    role: str = "staff"
    description: str = ""
    system_prompt: str = "You are a professional operator doing real client work."

    def __init__(self, cfg, ledger: Ledger, provider, gate=None, llm: LLM | None = None):
        self.cfg = cfg
        self.ledger = ledger
        self.provider = provider
        self.gate = gate or approval.HoldGate()
        self._llm = llm
        self.last_cost_cents = 0

    @abstractmethod
    def tick(self) -> "TickReport":
        """One pass of this role's work."""

    @property
    def llm(self) -> LLM:
        if self._llm is None:
            self._llm = LLM(self.cfg)
        return self._llm

    def think(self, job: Job | None, prompt: str, **kwargs) -> str:
        """Call Claude and bill the tokens to the job.

        Enforces the per-job spend cap *before* the call, so a runaway agent
        stops at a known number instead of after the invoice arrives.
        """
        kwargs.setdefault("system", self.system_prompt)
        if job is not None:
            fresh = self.ledger.get_job(job.id)
            if fresh.cost_cents >= self.cfg.max_llm_cost_cents_per_job:
                raise BudgetExceeded(
                    f"job {job.id} already spent {fresh.cost_cents}¢ of "
                    f"{self.cfg.max_llm_cost_cents_per_job}¢ budget"
                )
        completion = self.llm.complete(prompt, **kwargs)
        self.last_cost_cents = completion.cost_cents
        if job is not None:
            self.ledger.add_cost(job.id, completion.cost_cents)
        return completion.text

    def write_outbox(self, name: str, content: str) -> str:
        """Queue an outbound message for a human to actually send."""
        self.cfg.ensure_dirs()
        path: Path = self.cfg.outbox / name
        path.write_text(content)
        return str(path)

    def read_inbox(self, subdir: str) -> list[tuple[str, dict]]:
        """Read JSON drops from ``inbox/<subdir>``. Real demand comes from here."""
        import json

        folder = self.cfg.inbox / subdir
        if not folder.exists():
            return []
        out = []
        for path in sorted(folder.glob("*.json")):
            try:
                out.append((path.stem, json.loads(path.read_text())))
            except json.JSONDecodeError:
                continue
        return out


class EarningAgent(BaseAgent):
    """Adds the money path: quote → approve → invoice → settle → deliver."""

    payment_timing: str = PREPAY

    # ------------------------------------------------------------- subclass API

    @abstractmethod
    def find_opportunities(self) -> list[Opportunity]:
        """Read real inbound demand. No inventing customers."""

    @abstractmethod
    def qualify(self, opp: Opportunity) -> Quote | None:
        """Price the work, or return None to decline it."""

    @abstractmethod
    def produce(self, job: Job, payload: dict) -> Deliverable:
        """Do the billable work."""

    def deliver(self, job: Job, deliverable: Deliverable, payload: dict) -> str:
        """Hand the work over. Default: write to the outbox for a human to send."""
        self.cfg.ensure_dirs()
        path = self.cfg.deliverables / f"job-{job.id}-{deliverable.filename}"
        path.write_text(deliverable.content)
        return str(path)

    # ---------------------------------------------------------------- pipeline

    def tick(self) -> TickReport:
        """One full pass: ingest new demand, then advance everything in flight."""
        report = TickReport(agent=self.name)
        self._ingest(report)
        self._collect(report)
        self._fulfil(report)
        return report

    def _ingest(self, report: TickReport) -> None:
        for opp in self.find_opportunities():
            opp_id = self.ledger.record_opportunity(
                self.name, opp.source, opp.external_ref, opp.payload
            )
            if opp_id is None:
                continue  # already handled in an earlier tick
            opp.id = opp_id
            report.found += 1
            try:
                self._start(opp, report)
            except Refused as exc:
                report.errors.append(f"opportunity {opp.external_ref}: refused ({exc})")
                self.ledger.set_opportunity_status(opp_id, "failed")
            except Exception as exc:  # noqa: BLE001 - one bad lead must not stop the run
                report.errors.append(f"opportunity {opp.external_ref}: {type(exc).__name__}: {exc}")
                self.ledger.set_opportunity_status(opp_id, "failed")

    def _start(self, opp: Opportunity, report: TickReport) -> None:
        quote = self.qualify(opp)
        if quote is None:
            self.ledger.set_opportunity_status(opp.id, "declined")
            report.rejected += 1
            return

        margin = quote.amount_cents - quote.estimated_cost_cents
        if margin < self.cfg.min_margin_cents:
            self.ledger.set_opportunity_status(opp.id, "declined")
            self.ledger.log(
                "opportunity",
                opp.id,
                "declined_unprofitable",
                quote_cents=quote.amount_cents,
                estimated_cost_cents=quote.estimated_cost_cents,
            )
            report.rejected += 1
            return

        job = self.ledger.create_job(
            agent=self.name,
            title=opp.title,
            quote_cents=quote.amount_cents,
            currency=quote.currency,
            opportunity_id=opp.id,
        )
        self.ledger.update_job(job.id, notes=quote.scope)
        if quote.qualification_cost_cents:
            self.ledger.add_cost(job.id, quote.qualification_cost_cents)
        self.ledger.set_opportunity_status(opp.id, "accepted")
        report.quoted += 1

        decision = self.gate.review(
            Action(
                kind="quote",
                summary=f"{self.name}: quote for {opp.title}",
                body=quote.scope,
                amount_cents=quote.amount_cents,
                recipient=opp.customer_email,
            )
        )
        if not decision.ok:
            status = ledger_mod.REJECTED if decision.result == approval.REJECTED else ledger_mod.AWAITING_APPROVAL
            self.ledger.update_job(job.id, status=status)
            self.ledger.log("job", job.id, f"quote_{decision.result}", reason=decision.reason)
            report.held += decision.result == approval.HELD
            report.rejected += decision.result == approval.REJECTED
            return

        if self.payment_timing == PREPAY:
            self._invoice(job, opp.customer_email, quote.scope, report)
        else:
            self.ledger.update_job(job.id, status=ledger_mod.IN_PROGRESS)

    def _invoice(self, job: Job, customer_email: str, scope: str, report: TickReport) -> None:
        decision = self.gate.review(
            Action(
                kind="invoice",
                summary=f"{self.name}: send invoice for job {job.id}",
                body=scope,
                amount_cents=job.quote_cents,
                recipient=customer_email,
            )
        )
        if not decision.ok:
            self.ledger.update_job(job.id, status=ledger_mod.AWAITING_APPROVAL)
            self.ledger.log("job", job.id, f"invoice_{decision.result}", reason=decision.reason)
            report.held += 1
            return

        handle = self.provider.create_invoice(
            customer_email=customer_email,
            description=f"{job.title} — {self.name}",
            amount_cents=job.quote_cents,
            currency=job.currency,
            metadata={"job_id": job.id, "agent": self.name},
        )
        self.ledger.record_invoice(
            job_id=job.id,
            provider=handle.provider,
            provider_ref=handle.provider_ref,
            amount_cents=handle.amount_cents,
            currency=handle.currency,
            status=handle.status,
            url=handle.url,
        )
        self.ledger.update_job(job.id, status=ledger_mod.AWAITING_PAYMENT)
        report.invoiced += 1

    def _collect(self, report: TickReport) -> None:
        """Ask the provider which invoices actually settled. This is payday."""
        for invoice in self.ledger.open_invoices():
            job = self.ledger.get_job(invoice.job_id)
            if job.agent != self.name:
                continue
            try:
                settlement = self.provider.fetch_settlement(invoice.provider_ref)
            except Exception as exc:  # noqa: BLE001 - a provider blip is not a lost invoice
                report.errors.append(f"invoice {invoice.provider_ref}: {exc}")
                continue
            if settlement is None:
                continue
            if self.ledger.settle(
                invoice, settlement.event_id, settlement.amount_cents, settlement.currency
            ):
                report.settled_cents += settlement.amount_cents

    def _fulfil(self, report: TickReport) -> None:
        """Do and deliver the work for every job whose money is confirmed."""
        pending = self.ledger.jobs(status=ledger_mod.AWAITING_PAYMENT, agent=self.name)
        pending += self.ledger.jobs(status=ledger_mod.IN_PROGRESS, agent=self.name)
        for job in pending:
            if self.payment_timing == PREPAY and not self.ledger.job_is_paid(job.id):
                continue
            try:
                self._work(job, report)
            except Refused as exc:
                self.ledger.update_job(job.id, status=ledger_mod.FAILED, notes=f"refused: {exc}")
                report.errors.append(f"job {job.id}: refused ({exc})")
            except Exception as exc:  # noqa: BLE001 - one failed job must not stop the run
                self.ledger.update_job(job.id, status=ledger_mod.FAILED, notes=str(exc))
                report.errors.append(f"job {job.id}: {type(exc).__name__}: {exc}")

    def _work(self, job: Job, report: TickReport) -> None:
        self.ledger.update_job(job.id, status=ledger_mod.IN_PROGRESS)
        payload = self.ledger.opportunity_payload(job.opportunity_id)
        deliverable = self.produce(job, payload)

        decision = self.gate.review(
            Action(
                kind="deliverable",
                summary=f"{self.name}: deliver job {job.id} ({job.title})",
                body=deliverable.summary or deliverable.content,
                recipient=payload.get("email"),
            )
        )
        if not decision.ok:
            self.ledger.update_job(job.id, status=ledger_mod.AWAITING_APPROVAL)
            self.ledger.log("job", job.id, f"delivery_{decision.result}", reason=decision.reason)
            report.held += 1
            return

        location = self.deliver(job, deliverable, payload)
        self.ledger.update_job(job.id, status=ledger_mod.DELIVERED, deliverable=location)
        report.delivered += 1

        if self.payment_timing == POSTPAY and not self.ledger.invoices_for_job(job.id):
            self._invoice(job, payload.get("email", ""), job.notes or job.title, report)

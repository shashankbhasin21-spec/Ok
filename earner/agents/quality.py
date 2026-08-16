"""Quality — the last check before work reaches a paying client.

Reviews each finished deliverable against the scope that was actually invoiced,
with a fresh context and no attachment to the work. A fail sends the job back
rather than out. It also watches unit economics and flags jobs that cost more
to produce than they billed.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..agent import BaseAgent, TickReport
from ..ledger import AWAITING_APPROVAL, DELIVERED, IN_PROGRESS

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "revise", "fail"]},
        "meets_scope": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["verdict", "meets_scope", "issues", "unsupported_claims", "summary"],
    "additionalProperties": False,
}


class QualityAgent(BaseAgent):
    name = "quality"
    role = "Quality"
    description = "Reviews delivered work against invoiced scope and flags loss-making jobs."

    system_prompt = (
        "You review professional deliverables before a paying client sees them. You check one "
        "thing above all: does this deliver what was invoiced? You are specific about defects and "
        "you flag any claim that is asserted without support, because a confident wrong statement "
        "is what loses a client. You pass work that is genuinely good — a review that never passes "
        "anything is as useless as one that never fails anything."
    )

    def tick(self) -> TickReport:
        report = TickReport(agent=self.name)
        for job in self.ledger.jobs(status=DELIVERED):
            if self._reviewed(job.id):
                continue
            try:
                self._review(job, report)
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"job {job.id}: {type(exc).__name__}: {exc}")
        self._flag_economics(report)
        return report

    def _reviewed(self, job_id: int) -> bool:
        row = self.ledger.conn.execute(
            "SELECT 1 FROM events WHERE entity='job' AND entity_id=? AND kind='qa_reviewed' LIMIT 1",
            (job_id,),
        ).fetchone()
        return row is not None

    def _review(self, job, report: TickReport) -> None:
        if not job.deliverable or not Path(job.deliverable).exists():
            self.ledger.log("job", job.id, "qa_reviewed", verdict="fail", reason="no deliverable file")
            report.errors.append(f"job {job.id}: delivered but no file on disk")
            return

        content = Path(job.deliverable).read_text()
        raw = self.think(
            job,
            "Review this deliverable against the scope it was invoiced for.\n\n"
            f"Invoiced scope: {job.notes}\n"
            f"Client paid: ${job.quote_cents / 100:,.2f}\n\n"
            f"Deliverable:\n\n{content[:60000]}\n\n"
            "verdict='pass' if a client would accept this as-is; 'revise' for fixable gaps; "
            "'fail' if it does not deliver the scope. List unsupported claims separately — "
            "anything stated as fact that the document gives no basis for.",
            schema=REVIEW_SCHEMA,
            effort="medium",
        )
        review = json.loads(raw)
        self.ledger.log("job", job.id, "qa_reviewed", **review)

        if review["verdict"] == "pass":
            report.delivered += 1
            return

        # Send it back: the job leaves DELIVERED so delivery reworks it.
        self.ledger.update_job(
            job.id,
            status=IN_PROGRESS if review["verdict"] == "revise" else AWAITING_APPROVAL,
            notes=f"{job.notes}\n\nQA ({review['verdict']}): {'; '.join(review['issues'][:5])}",
        )
        report.rejected += 1
        self.write_outbox(
            f"qa-job-{job.id}.md",
            f"# QA {review['verdict']} — job {job.id}: {job.title}\n\n"
            f"{review['summary']}\n\n## Issues\n"
            + "\n".join(f"- {i}" for i in review["issues"])
            + "\n\n## Unsupported claims\n"
            + ("\n".join(f"- {c}" for c in review["unsupported_claims"]) or "- none found"),
        )

    def _flag_economics(self, report: TickReport) -> None:
        """Loud about jobs that lost money. Quiet failure here is how a
        business runs for a month before noticing its pricing is wrong."""
        for job in self.ledger.jobs():
            if job.quote_cents and job.margin_cents < 0:
                if not self._flagged(job.id):
                    self.ledger.log(
                        "job",
                        job.id,
                        "loss_making",
                        quote_cents=job.quote_cents,
                        cost_cents=job.cost_cents,
                        margin_cents=job.margin_cents,
                    )
                    report.errors.append(
                        f"job {job.id} lost ${abs(job.margin_cents) / 100:,.2f} "
                        f"(billed ${job.quote_cents / 100:,.2f}, cost ${job.cost_cents / 100:,.2f})"
                    )

    def _flagged(self, job_id: int) -> bool:
        row = self.ledger.conn.execute(
            "SELECT 1 FROM events WHERE entity='job' AND entity_id=? AND kind='loss_making' LIMIT 1",
            (job_id,),
        ).fetchone()
        return row is not None

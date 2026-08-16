"""The ledger's job is to be un-foolable about money."""

from __future__ import annotations

from earner.ledger import DELIVERED


def test_invoicing_is_not_revenue(ledger):
    job = ledger.create_job("delivery", "Market brief", 50_000, "usd")
    invoice = ledger.record_invoice(job.id, "sandbox", "sbx_1", 50_000, "usd", "open", None)

    assert ledger.revenue_cents() == 0, "an unpaid invoice must never count as revenue"
    assert ledger.outstanding_cents() == 50_000
    assert not ledger.job_is_paid(job.id)

    ledger.settle(invoice, "evt_1", 50_000, "usd")
    assert ledger.revenue_cents() == 50_000
    assert ledger.outstanding_cents() == 0
    assert ledger.job_is_paid(job.id)


def test_settlement_is_idempotent(ledger):
    job = ledger.create_job("delivery", "Analysis", 20_000, "usd")
    invoice = ledger.record_invoice(job.id, "sandbox", "sbx_2", 20_000, "usd", "open", None)

    assert ledger.settle(invoice, "evt_same", 20_000, "usd") is True
    assert ledger.settle(invoice, "evt_same", 20_000, "usd") is False, "replay must be ignored"
    assert ledger.revenue_cents() == 20_000


def test_opportunities_deduplicate(ledger):
    first = ledger.record_opportunity("delivery", "inbox", "req-1", {"brief": "x"})
    second = ledger.record_opportunity("delivery", "inbox", "req-1", {"brief": "x"})
    assert first is not None
    assert second is None, "the same inbound request must not become two jobs"


def test_margin_and_summary(ledger):
    job = ledger.create_job("delivery", "Deep report", 30_000, "usd")
    ledger.add_cost(job.id, 450)
    invoice = ledger.record_invoice(job.id, "sandbox", "sbx_3", 30_000, "usd", "open", None)
    ledger.settle(invoice, "evt_3", 30_000, "usd")
    ledger.update_job(job.id, status=DELIVERED)

    assert ledger.get_job(job.id).margin_cents == 29_550
    summary = ledger.summary(["delivery"])
    assert summary["delivery"]["revenue_cents"] == 30_000
    assert summary["delivery"]["cost_cents"] == 450
    assert summary["TOTAL"]["net_cents"] == 29_550

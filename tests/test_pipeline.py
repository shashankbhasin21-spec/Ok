"""End-to-end: an inbound request becomes an invoice, and work only happens
once the money is confirmed."""

from __future__ import annotations

import json

from earner import approval, ledger as L
from earner.agents.service_desk import ServiceDeskAgent
from earner.agent import PREPAY

from conftest import FakeLLM, write_request

QUOTE = json.dumps(
    {
        "accept": True,
        "reason": "clear brief",
        "price_cents": 45_000,
        "scope": "12-page competitive brief with sources",
        "estimated_output_words": 2500,
    }
)
DECLINE = json.dumps(
    {
        "accept": False,
        "reason": "brief needs data we do not have",
        "price_cents": 0,
        "scope": "",
        "estimated_output_words": 0,
    }
)


def _agent(cfg, ledger, provider, responses, gate=None):
    return ServiceDeskAgent(
        cfg, ledger, provider, gate or approval.AutoGate(), FakeLLM(responses)
    )


def test_no_work_before_payment(cfg, ledger, provider):
    write_request(cfg, "req-1", email="client@example.com", title="Competitor brief",
                  brief="Compare the three biggest vendors in X.")
    agent = _agent(cfg, ledger, provider, [QUOTE, "THE DELIVERABLE"])

    report = agent.tick()
    assert report.invoiced == 1
    job = ledger.jobs(agent="delivery")[0]
    assert job.status == L.AWAITING_PAYMENT
    assert job.deliverable is None, "work must not start before the invoice settles"
    assert ledger.revenue_cents() == 0

    # The customer pays for real; the provider now says so.
    invoice = ledger.invoices_for_job(job.id)[0]
    provider.mark_paid(invoice.provider_ref)

    report = agent.tick()
    assert report.settled_cents == 45_000
    assert report.delivered == 1
    job = ledger.get_job(job.id)
    assert job.status == L.DELIVERED
    assert job.deliverable and "THE DELIVERABLE" in open(job.deliverable).read()
    assert ledger.revenue_cents() == 45_000


def test_declined_brief_never_becomes_a_job(cfg, ledger, provider):
    write_request(cfg, "req-2", email="c@example.com", title="Vague", brief="do something")
    agent = _agent(cfg, ledger, provider, [DECLINE])

    report = agent.tick()
    assert report.rejected == 1
    assert ledger.jobs(agent="delivery") == []


def test_unprofitable_work_is_refused(cfg, ledger, provider):
    """A big ask on a tiny budget costs more in tokens than it bills."""
    write_request(cfg, "req-3", email="c@example.com", title="Tiny budget",
                  brief="An exhaustive 40,000-word market study.", budget_cents=300)
    cheap = json.dumps({**json.loads(QUOTE), "price_cents": 5_000, "estimated_output_words": 40_000})
    agent = _agent(cfg, ledger, provider, [cheap])

    report = agent.tick()
    assert report.rejected == 1
    assert ledger.jobs(agent="delivery") == []


def test_hold_gate_parks_the_job(cfg, ledger, provider):
    """With no approver, nothing reaches the customer — it waits."""
    write_request(cfg, "req-4", email="c@example.com", title="Brief", brief="Analyse X for us.")
    agent = _agent(cfg, ledger, provider, [QUOTE], gate=approval.HoldGate())

    agent.tick()
    job = ledger.jobs(agent="delivery")[0]
    assert job.status == L.AWAITING_APPROVAL
    assert ledger.open_invoices() == [], "no invoice may be sent without approval"


def test_costs_are_billed_to_the_job(cfg, ledger, provider):
    write_request(cfg, "req-5", email="c@example.com", title="Brief", brief="Analyse X.")
    agent = _agent(cfg, ledger, provider, [QUOTE, "body"])
    agent.tick()
    job = ledger.jobs(agent="delivery")[0]
    invoice = ledger.invoices_for_job(job.id)[0]
    provider.mark_paid(invoice.provider_ref)
    agent.tick()

    job = ledger.get_job(job.id)
    assert job.cost_cents > 0, "qualification and production tokens must be billed to the job"
    assert job.margin_cents == job.quote_cents - job.cost_cents


def test_prepay_is_the_default(cfg, ledger, provider):
    assert ServiceDeskAgent.payment_timing == PREPAY

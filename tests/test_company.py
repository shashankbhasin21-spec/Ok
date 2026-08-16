"""Targets, collections, quality, and booting the whole firm."""

from __future__ import annotations

import json
import time

from earner import approval, goals
from earner.agents.collections import CollectionsAgent
from earner.agents.quality import QualityAgent
from earner.ledger import DELIVERED
from earner.platform import Platform

from conftest import FakeLLM


def test_target_reports_honestly_about_unpaid_invoices(ledger):
    target = goals.Target(amount_cents=500_000, hours=50, started_at=time.time() - 3600)

    job = ledger.create_job("delivery", "Big brief", 500_000, "usd")
    invoice = ledger.record_invoice(job.id, "sandbox", "r1", 500_000, "usd", "open", None)

    status = goals.evaluate(target, ledger)
    assert status.settled_cents == 0
    assert "AT RISK" in status.verdict, "invoiced-but-unpaid must not read as success"

    ledger.settle(invoice, "e1", 500_000, "usd")
    assert "HIT" in goals.evaluate(target, ledger).verdict


def test_target_plan_translates_a_gap_into_deals(ledger):
    target = goals.Target(amount_cents=500_000, hours=50, started_at=time.time())
    plan = goals.plan_for(goals.evaluate(target, ledger), average_deal_cents=50_000)
    assert plan["deals_needed"] == 10
    assert plan["leads_needed_at_30pct_close"] == 30


def test_collections_escalates_then_stops_when_paid(cfg, ledger, provider):
    job = ledger.create_job("delivery", "Delivered work", 40_000, "usd")
    ledger.update_job(job.id, status=DELIVERED, notes="a report")
    handle = provider.create_invoice(
        customer_email="c@example.com", description="work", amount_cents=40_000, currency="usd"
    )
    invoice = ledger.record_invoice(
        job.id, handle.provider, handle.provider_ref, 40_000, "usd", "open", handle.url
    )
    # Backdate the invoice so it is genuinely overdue.
    ledger.conn.execute(
        "UPDATE invoices SET created_at=? WHERE id=?", (time.time() - 12 * 86400, invoice.id)
    )
    ledger.conn.commit()

    agent = CollectionsAgent(cfg, ledger, provider, approval.AutoGate(), FakeLLM(["Please pay."]))
    report = agent.tick()
    assert report.quoted == 1, "an overdue invoice should be chased"
    assert (cfg.outbox / f"collections-{invoice.id}-follow_up.txt").exists()

    assert agent.tick().quoted == 0, "the same stage must not be sent twice"

    provider.mark_paid(handle.provider_ref)
    report = agent.tick()
    assert report.settled_cents == 40_000
    assert ledger.revenue_cents() == 40_000


def test_quality_sends_bad_work_back(cfg, ledger, provider):
    job = ledger.create_job("delivery", "Report", 30_000, "usd")
    path = cfg.deliverables / "job.md"
    path.write_text("Thin, generic content with no sources.")
    ledger.update_job(job.id, status=DELIVERED, deliverable=str(path), notes="sourced analysis")

    verdict = json.dumps(
        {
            "verdict": "revise",
            "meets_scope": False,
            "issues": ["no sources", "does not cover the agreed scope"],
            "unsupported_claims": ["market is growing fast"],
            "summary": "Not deliverable as-is.",
        }
    )
    agent = QualityAgent(cfg, ledger, provider, approval.AutoGate(), FakeLLM([verdict]))
    report = agent.tick()

    assert report.rejected == 1
    assert ledger.get_job(job.id).status != DELIVERED, "failed QA must not stay delivered"
    assert (cfg.outbox / f"qa-job-{job.id}.md").exists()


def test_quality_flags_loss_making_jobs(cfg, ledger, provider):
    job = ledger.create_job("delivery", "Underpriced", 500, "usd")
    ledger.add_cost(job.id, 900)
    agent = QualityAgent(cfg, ledger, provider, approval.AutoGate(), FakeLLM([]))
    report = agent.tick()
    assert any("lost" in e for e in report.errors)


def test_platform_boots_with_full_staff(cfg):
    platform = Platform(cfg, gate_name="hold")
    try:
        assert set(platform.staff) == {
            "acquisition", "delivery", "product", "growth", "collections", "quality",
        }
        assert platform.ceo.staff is platform.staff
        readiness = dict((name, ok) for name, ok, _ in platform.readiness())
        assert readiness["Payments (live)"] is False, "sandbox must never look live"
    finally:
        platform.close()


def test_autoapprove_refuses_live_without_explicit_optin():
    import pytest

    with pytest.raises(ValueError, match="allow_live"):
        approval.AutoGate(is_live=True)

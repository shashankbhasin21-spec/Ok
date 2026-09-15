"""Firm platform tests — pipeline, permissions, dedupe, settlement, review."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from earner.firm.authorization import Authorization, AuthorizationError
from earner.firm.capabilities import score_fit
from earner.firm.coordinator import Coordinator
from earner.firm.models import AgentRole, OppStatus
from earner.firm.payouts import PayoutError, PayoutStore
from earner.firm.review import pipeline_metrics, run_hourly_review
from earner.firm.store import FirmStore, IllegalTransition
from earner.firm.vertical_slice import run_vertical_slice
from earner.payments import SandboxProvider


@pytest.fixture
def workdir(tmp_path):
    d = tmp_path / "firm"
    d.mkdir()
    return d


@pytest.fixture
def store(workdir):
    s = FirmStore(workdir / "firm.db")
    yield s
    s.close()


@pytest.fixture
def coord(store, workdir):
    return Coordinator(store, workdir, provider=SandboxProvider(workdir / "sbx.json"))


def test_forbidden_work_rejected():
    score, reasons, reject = score_fit(
        title="Train LLM on GPU cluster",
        description="model training at scale with fake reviews",
        skills=["gpu"],
        budget_cents=500_000,
    )
    assert score == 0.0
    assert reject


def test_landing_page_fits():
    score, reasons, reject = score_fit(
        title="Landing page for SaaS",
        description="Need a website landing page in HTML/CSS",
        skills=["landing page", "website"],
        budget_cents=180_000,
    )
    assert reject is None
    assert score >= 0.35


def test_pipeline_transitions(store):
    opp = store.import_opportunity(
        source="test",
        external_id="t1",
        title="Landing page build",
        description="website landing page",
        skills=["landing page"],
        budget_cents=150_000,
        simulated=True,
    )
    assert opp.status == OppStatus.DISCOVERED.value
    store.advance(opp.id, OppStatus.QUALIFIED)
    with pytest.raises(IllegalTransition):
        store.advance(opp.id, OppStatus.PAID)


def test_duplicate_opportunity_idempotent(store):
    a = store.import_opportunity(source="s", external_id="x", title="A", simulated=True)
    b = store.import_opportunity(source="s", external_id="x", title="A", simulated=True)
    assert a is not None
    assert b is None


def test_agent_cannot_use_foreign_tool(store):
    auth = Authorization(store)
    with pytest.raises(AuthorizationError):
        auth.check_tool(AgentRole.RESEARCHER, "create_invoice")
    with pytest.raises(AuthorizationError):
        auth.check_tool(AgentRole.FINANCE, "change_payout")  # not even in any role


def test_max_concurrent_agents(store, workdir):
    store.set_meta("max_concurrent_agents", "2")
    auth = Authorization(store)
    store.start_agent(role="co_agent", name="a")
    store.start_agent(role="co_agent", name="b")
    with pytest.raises(AuthorizationError):
        auth.assert_can_spawn()


def test_standing_auth_bounds(store):
    auth = Authorization(store)
    d = auth.check_external_action(
        platform="upwork", service="landing_page", price_cents=10000, kind="message"
    )
    assert d.requires_approval
    store.upsert_standing_auth(
        platform="upwork",
        service="landing_page",
        max_price_cents=50_000,
        daily_volume=5,
        daily_spend_cents=100_000,
    )
    ok = auth.check_external_action(
        platform="upwork", service="landing_page", price_cents=10_000, kind="message"
    )
    assert ok.allowed
    submit = auth.check_external_action(
        platform="upwork", service="landing_page", price_cents=10_000, kind="submit_proposal"
    )
    assert submit.requires_approval


def test_pause_and_circuit_breaker(store, coord):
    store.set_paused(True)
    r = coord.run_agent("qualification", opportunity_id="missing")
    assert not r.ok
    assert "paused" in r.error
    store.set_paused(False)
    store.set_meta("cost_circuit_breaker_cents", "1")
    store.start_agent(role="engineering", name="burn")
    # finish with cost
    runs = store.list_agent_runs()
    store.finish_agent(runs[0]["id"], cost_cents=5)
    r = coord.run_agent("opportunity_researcher", opportunities=[])
    assert not r.ok
    assert store.is_paused()


def test_payout_requires_reauth_and_masks(workdir, monkeypatch):
    monkeypatch.setenv("FIRM_OWNER_SECRET", "test-secret")
    ps = PayoutStore(workdir / "payouts.enc.json")
    with pytest.raises(PayoutError):
        ps.save({"bank_account_number": "1234567890", "bank_name": "Test"}, "bad")
    token = ps.authenticate("test-secret")
    view = ps.save(
        {
            "bank_account_number": "1234567890",
            "bank_name": "Test Bank",
            "account_holder": "Owner",
            "upi_id": "owner@upi",
            "routing_or_ifsc": "TEST0001",
        },
        token,
    )
    assert view["configured"]
    assert view["bank_account_last4"] == "7890"
    assert "1234567890" not in json.dumps(view)
    assert view["upi_id_masked"].startswith("ow")
    # Agents cannot save
    token2 = ps.authenticate("test-secret")
    with pytest.raises(PayoutError):
        ps.save({"_actor": "finance", "bank_account_number": "999"}, token2)


def test_vertical_slice_end_to_end(workdir):
    report = run_vertical_slice(workdir / "slice", mark_paid=True)
    assert report["ok"], report
    assert report["preview"]
    assert Path(report["preview"]).exists()
    assert report["settled_simulated"] is False
    assert report.get("settle_refused")
    fin = report["metrics"]["finance_usd"]
    assert fin["gross_revenue_cents"] == 0, "sandbox must never count as settled cash"
    assert any(s["step"] == "import_dedupe" and s["count"] == 0 for s in report["steps"])


def test_sandbox_confirm_payment_refused(store):
    inv = store.record_firm_invoice(
        project_id="prj_x",
        provider="sandbox",
        provider_ref="sbx_1",
        amount_cents=1000,
        currency="usd",
        lifecycle="pending",
        simulated=True,
    )
    with pytest.raises(ValueError, match="sandbox"):
        store.confirm_payment(inv["id"], "evt", 1000, "usd")
    assert store.gross_revenue_cents() == 0


def test_submission_idempotency(coord, store):
    opp = store.import_opportunity(
        source="sample",
        external_id="idem-1",
        title="Landing page site",
        description="website landing page html css",
        skills=["landing page", "website"],
        budget_cents=200_000,
        simulated=True,
    )
    coord.run_agent("qualification", opportunity_id=opp.id)
    prop = coord.run_agent("proposal", opportunity_id=opp.id)
    aid = prop.output["approval_id"]
    coord.process_approval(aid, approved=True)
    # Second approval decision on already-advanced opp should not duplicate submit task crash
    key = store.get_opportunity(opp.id).submission_key
    assert key
    task = store.enqueue_task(kind="record_submission", idempotency_key=key, payload={})
    # Returns existing task (completed), not a new insert failure
    assert task is not None
    assert task["idempotency_key"] == key


def test_hourly_review_zero_revenue_guidance(coord, store):
    store.import_opportunity(
        source="sample",
        external_id="rev-1",
        title="Landing page",
        description="landing page website",
        skills=["landing page"],
        budget_cents=150_000,
        simulated=True,
    )
    out = run_hourly_review(coord)
    assert out["bottleneck"]
    assert out["metrics"]["finance_usd"]["gross_revenue_cents"] == 0
    findings = store.latest_review()["findings"]
    assert findings["zero_revenue_guidance"]["do_not_assume_failure"] is True


def test_audit_redacts_secrets(store):
    store.audit("system", "test", "job", "1", api_key="sk-secret", title="ok")
    row = store.recent_audit(1)[0]
    detail = json.loads(row["detail_json"])
    assert detail["api_key"] == "***REDACTED***"
    assert detail["title"] == "ok"

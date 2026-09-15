"""Coordinator — task graph, priorities, resource limits, co-agent lifecycle."""

from __future__ import annotations

import time
from typing import Any

from .agents import (
    AgentResult,
    DeliveryPlanner,
    EngineeringWorker,
    ExperimentAnalyst,
    FinanceAgent,
    IndependentReviewer,
    OpportunityResearcher,
    ProposalAgent,
    QualificationAgent,
)
from .authorization import Authorization, AuthorizationError
from .models import AgentRole, OppStatus
from .offers import capacity_snapshot, offer_by_id
from .store import FirmStore


class EvidenceRequired(ValueError):
    pass


class CapacityExceeded(ValueError):
    pass


class Coordinator:
    """Owns scheduling. Cannot grant itself payout changes or unbounded spawn."""

    def __init__(self, store: FirmStore, workdir, provider=None):
        self.store = store
        self.workdir = workdir
        self.provider = provider
        self.auth = Authorization(store)

    def pause(self) -> None:
        self.auth.check_tool(AgentRole.COORDINATOR, "pause_firm")
        self.store.set_paused(True)

    def resume(self) -> None:
        self.store.set_paused(False)

    def spawn_co_agent(
        self,
        *,
        hypothesis: str,
        deliverable: str,
        cost_limit_cents: int,
        expires_in_sec: int = 3600,
    ) -> dict:
        self.auth.check_tool(AgentRole.COORDINATOR, "spawn_co_agent")
        self.auth.assert_can_spawn()
        if not hypothesis or not deliverable:
            raise AuthorizationError("co-agents require a hypothesis and deliverable")
        if cost_limit_cents <= 0:
            raise AuthorizationError("co-agents require a positive cost limit")
        run = self.store.start_agent(
            role=AgentRole.CO_AGENT.value,
            name="specialist_co_agent",
            hypothesis=hypothesis,
            deliverable=deliverable,
            cost_budget_cents=cost_limit_cents,
            time_budget_sec=expires_in_sec,
            expires_at=time.time() + expires_in_sec,
        )
        return run

    def reap_expired(self) -> list[str]:
        stopped = []
        now = time.time()
        for run in self.store.list_agent_runs(active_only=True):
            if run.get("expires_at") and run["expires_at"] < now:
                self.store.cancel_agent(run["id"], "expired")
                stopped.append(run["id"])
                self.auth.check_tool(AgentRole.COORDINATOR, "merge_agent")
        return stopped

    def run_agent(self, role: str, **kwargs: Any) -> AgentResult:
        if self.store.is_paused():
            return AgentResult(False, error="firm is paused")
        breaker = int(self.store.get_meta("cost_circuit_breaker_cents", "5000"))
        if self.store.total_agent_cost_cents() >= breaker:
            self.pause()
            return AgentResult(False, error="cost circuit breaker — firm paused")

        agents = {
            "opportunity_researcher": OpportunityResearcher,
            "qualification": QualificationAgent,
            "proposal": ProposalAgent,
            "delivery_planner": DeliveryPlanner,
            "engineering": EngineeringWorker,
            "independent_reviewer": IndependentReviewer,
            "finance": FinanceAgent,
            "experiment_analyst": ExperimentAnalyst,
        }
        cls = agents.get(role)
        if cls is None:
            return AgentResult(False, error=f"unknown role {role}")
        agent = cls(self.store, self.auth, workdir=self.workdir)
        if role == "finance" and self.provider is not None:
            kwargs.setdefault("provider", self.provider)
        try:
            return agent.run(**kwargs)
        except AuthorizationError as exc:
            return AgentResult(False, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            return AgentResult(False, error=f"{type(exc).__name__}: {exc}")

    def process_approval(self, approval_id: str, *, approved: bool, reason: str = "") -> dict:
        """Owner approval of a proposal. Does NOT mean externally submitted."""
        apr = self.store.decide_approval(approval_id, approved=approved, reason=reason)
        if apr["entity_type"] == "opportunity" and apr["kind"] == "proposal":
            oid = apr["entity_id"]
            opp = self.store.get_opportunity(oid)
            if approved:
                if opp.status == OppStatus.AWAITING_APPROVAL.value:
                    self.store.advance(oid, OppStatus.APPROVED)
            else:
                if opp.status == OppStatus.AWAITING_APPROVAL.value:
                    self.store.advance(oid, OppStatus.REJECTED)
        return apr

    def record_external_submission(
        self,
        opportunity_id: str,
        *,
        evidence_kind: str,
        evidence_ref: str,
        note: str = "",
    ) -> dict:
        """Mark proposal as submitted externally — requires board/message evidence."""
        opp = self.store.get_opportunity(opportunity_id)
        if opp.status != OppStatus.APPROVED.value:
            raise EvidenceRequired(f"expected approved, got {opp.status}")
        self.store.add_evidence(
            opportunity_id=opportunity_id,
            kind=evidence_kind,
            reference=evidence_ref,
            for_status="submitted",
            note=note,
        )
        key = f"submit:{opportunity_id}:v{opp.proposal_version}"
        task = self.store.enqueue_task(
            kind="record_submission",
            idempotency_key=key,
            payload={"opportunity_id": opportunity_id, "evidence_ref": evidence_ref},
        )
        self.store.update_opportunity(
            opportunity_id,
            submission_key=key,
            submission_receipt=evidence_ref,
        )
        self.store.advance(opportunity_id, OppStatus.SUBMITTED)
        if task and task.get("status") == "queued":
            self.store.complete_task(task["id"], {"receipt": evidence_ref})
        return self.store.get_opportunity(opportunity_id).to_dict()

    def record_buyer_reply(
        self,
        opportunity_id: str,
        *,
        evidence_kind: str,
        evidence_ref: str,
        note: str = "",
    ) -> dict:
        opp = self.store.get_opportunity(opportunity_id)
        if opp.status != OppStatus.SUBMITTED.value:
            raise EvidenceRequired(f"expected submitted, got {opp.status}")
        self.store.add_evidence(
            opportunity_id=opportunity_id,
            kind=evidence_kind,
            reference=evidence_ref,
            for_status="replied",
            note=note,
        )
        self.store.advance(opportunity_id, OppStatus.REPLIED)
        return self.store.get_opportunity(opportunity_id).to_dict()

    def mark_signed(
        self,
        opportunity_id: str,
        *,
        evidence_kind: str,
        evidence_ref: str,
        note: str = "",
        offer_id: str | None = None,
    ) -> dict:
        """Signed booking — contract/SOW/platform hire evidence required. No silent won."""
        opp = self.store.get_opportunity(opportunity_id)
        if opp.status not in (OppStatus.REPLIED.value, OppStatus.SUBMITTED.value):
            raise EvidenceRequired(f"cannot sign from status {opp.status}")
        if evidence_kind not in ("contract_pdf", "sow_acceptance", "platform_hire_receipt"):
            raise EvidenceRequired(
                "signed bookings require contract_pdf, sow_acceptance, or platform_hire_receipt"
            )
        # Capacity check before accepting
        service = offer_id or (opp.proposal or {}).get("service")
        if service:
            cap = self.delivery_capacity()
            row = next((r for r in cap["offers"] if r["offer_id"] == service), None)
            if row and row["free"] <= 0:
                raise CapacityExceeded(
                    f"no delivery capacity for offer {service}; refuse booking inflation"
                )

        self.store.add_evidence(
            opportunity_id=opportunity_id,
            kind=evidence_kind,
            reference=evidence_ref,
            for_status="won",
            note=note,
        )
        if opp.status == OppStatus.SUBMITTED.value:
            self.store.add_evidence(
                opportunity_id=opportunity_id,
                kind="owner_attestation_with_artifact",
                reference=evidence_ref,
                for_status="replied",
                note="implied by signed hire evidence",
            )
            self.store.advance(opportunity_id, OppStatus.REPLIED)
        self.store.advance(opportunity_id, OppStatus.WON)
        return self.store.get_opportunity(opportunity_id).to_dict()

    def delivery_capacity(self) -> dict:
        active = {}
        for p in self.store.list_projects():
            if p.get("status") in ("active", "built", "revision_required"):
                try:
                    opp = self.store.get_opportunity(p["opportunity_id"])
                except KeyError:
                    continue
                svc = (opp.proposal or {}).get("service") or "landing_page"
                active[svc] = active.get(svc, 0) + 1
        return capacity_snapshot(active)

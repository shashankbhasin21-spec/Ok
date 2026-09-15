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
from .store import FirmStore


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
        """Stop co-agents past expiry or duplicate inactive specialists."""
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
        apr = self.store.decide_approval(approval_id, approved=approved, reason=reason)
        if apr["entity_type"] == "opportunity" and apr["kind"] == "proposal":
            oid = apr["entity_id"]
            if approved:
                # Mark submitted with idempotent key — manual handoff receipt.
                key = f"submit:{oid}:v{self.store.get_opportunity(oid).proposal_version}"
                task = self.store.enqueue_task(
                    kind="record_submission",
                    idempotency_key=key,
                    payload={"opportunity_id": oid},
                )
                opp = self.store.get_opportunity(oid)
                if opp.status == OppStatus.AWAITING_APPROVAL.value:
                    self.store.update_opportunity(
                        oid,
                        submission_key=key,
                        submission_receipt=f"manual-handoff:{key}",
                    )
                    self.store.advance(oid, OppStatus.SUBMITTED)
                if task and task.get("status") == "queued":
                    self.store.complete_task(task["id"], {"receipt": f"manual-handoff:{key}"})
            else:
                if self.store.get_opportunity(oid).status == OppStatus.AWAITING_APPROVAL.value:
                    self.store.advance(oid, OppStatus.REJECTED)
        return apr

    def mark_won(self, opportunity_id: str) -> dict:
        opp = self.store.get_opportunity(opportunity_id)
        if opp.status == OppStatus.SUBMITTED.value:
            self.store.advance(opportunity_id, OppStatus.REPLIED)
            opp = self.store.get_opportunity(opportunity_id)
        if opp.status == OppStatus.REPLIED.value:
            self.store.advance(opportunity_id, OppStatus.WON)
        elif opp.status == OppStatus.SUBMITTED.value:
            self.store.advance(opportunity_id, OppStatus.WON)
        return self.store.get_opportunity(opportunity_id).to_dict()

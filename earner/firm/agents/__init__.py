"""Bounded specialist agents for the firm.

Each agent has a narrow role, tool permissions, budgets, retries, and structured
output. Agents cannot grant permissions, change payouts, or spawn unboundedly.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..authorization import Authorization, AuthorizationError
from ..capabilities import infer_service, score_fit
from ..delivery import DeliveryWorkspace
from ..models import AgentRole, OppStatus
from ..store import FirmStore


@dataclass
class AgentResult:
    ok: bool
    output: dict = field(default_factory=dict)
    error: str = ""
    cost_cents: int = 0
    tokens_used: int = 0


class BaseFirmAgent(ABC):
    role: AgentRole
    name: str = "agent"

    def __init__(
        self,
        store: FirmStore,
        auth: Authorization,
        *,
        workdir,
        cost_budget_cents: int = 200,
        time_budget_sec: int = 600,
        max_retries: int = 2,
    ):
        self.store = store
        self.auth = auth
        self.workdir = workdir
        self.cost_budget_cents = cost_budget_cents
        self.time_budget_sec = time_budget_sec
        self.max_retries = max_retries
        self._run_id: str | None = None
        self._started = 0.0
        self._cost = 0

    def use_tool(self, tool: str) -> None:
        self.auth.check_tool(self.role, tool)

    def _begin(self, hypothesis: str = "", deliverable: str = "", parent_id: str | None = None) -> str:
        self.auth.assert_can_spawn()
        run = self.store.start_agent(
            role=self.role.value,
            name=self.name,
            hypothesis=hypothesis,
            deliverable=deliverable,
            parent_id=parent_id,
            cost_budget_cents=self.cost_budget_cents,
            time_budget_sec=self.time_budget_sec,
            max_retries=self.max_retries,
        )
        self._run_id = run["id"]
        self._started = time.time()
        self._cost = 0
        return run["id"]

    def _charge(self, cents: int, tokens: int = 0) -> None:
        self._cost += cents
        if self._cost > self.cost_budget_cents:
            raise AuthorizationError(
                f"agent {self.name} exceeded cost budget ${self.cost_budget_cents / 100:.2f}"
            )
        if time.time() - self._started > self.time_budget_sec:
            raise AuthorizationError(f"agent {self.name} exceeded time budget")

    def _finish(self, result: AgentResult) -> AgentResult:
        if self._run_id:
            self.store.finish_agent(
                self._run_id,
                status="completed" if result.ok else "failed",
                output=result.output,
                error=result.error,
                cost_cents=result.cost_cents or self._cost,
                tokens_used=result.tokens_used,
            )
        return result

    @abstractmethod
    def run(self, **kwargs: Any) -> AgentResult:
        ...


class OpportunityResearcher(BaseFirmAgent):
    role = AgentRole.RESEARCHER
    name = "opportunity_researcher"

    def run(self, opportunities: list[dict] | None = None, **_) -> AgentResult:
        rid = self._begin(deliverable="imported opportunities")
        self.use_tool("import_opportunity")
        imported = []
        for raw in opportunities or []:
            opp = self.store.import_opportunity(
                source=raw.get("source", "manual"),
                external_id=raw["external_id"],
                title=raw["title"],
                source_url=raw.get("source_url", ""),
                description=raw.get("description", ""),
                posted_at=raw.get("posted_at"),
                eligibility=raw.get("eligibility", ""),
                budget_cents=raw.get("budget_cents"),
                budget_currency=raw.get("budget_currency", "usd"),
                scope=raw.get("scope", ""),
                deadline=raw.get("deadline", ""),
                skills=raw.get("skills") or [],
                client_signals=raw.get("client_signals", ""),
                simulated=bool(raw.get("simulated", False)),
            )
            if opp:
                imported.append(opp.id)
            self._charge(1)
        return self._finish(
            AgentResult(True, {"imported_ids": imported, "count": len(imported), "run_id": rid})
        )


class QualificationAgent(BaseFirmAgent):
    role = AgentRole.QUALIFIER
    name = "qualification"

    def run(self, opportunity_id: str, **_) -> AgentResult:
        self._begin(deliverable=f"qualify {opportunity_id}")
        self.use_tool("score_fit")
        self.use_tool("check_capability")
        opp = self.store.get_opportunity(opportunity_id)
        score, reasons, reject = score_fit(
            title=opp.title,
            description=opp.description,
            skills=opp.skills or [],
            budget_cents=opp.budget_cents,
            budget_currency=opp.budget_currency,
        )
        self.store.update_opportunity(
            opportunity_id, fit_score=score, fit_reasons=reasons
        )
        self._charge(2)
        if reject or score < 0.35:
            self.store.advance(opportunity_id, OppStatus.REJECTED)
            return self._finish(
                AgentResult(True, {"qualified": False, "score": score, "reasons": reasons, "reject": reject})
            )
        self.store.advance(opportunity_id, OppStatus.QUALIFIED)
        return self._finish(
            AgentResult(True, {"qualified": True, "score": score, "reasons": reasons, "service": infer_service(opp.title, opp.description, opp.skills or [])})
        )


class ProposalAgent(BaseFirmAgent):
    role = AgentRole.PROPOSAL
    name = "proposal"

    VERIFIED_CREDENTIALS = [
        "Delivers websites, landing pages, workflow automation, and scoped software fixes",
        "Works under owner approval with isolated workspaces and independent review",
        "Does not claim model-training expertise or infrastructure we do not operate",
    ]

    def run(self, opportunity_id: str, **_) -> AgentResult:
        self._begin(deliverable=f"proposal {opportunity_id}")
        self.use_tool("draft_proposal")
        self.use_tool("read_credentials")
        opp = self.store.get_opportunity(opportunity_id)
        if opp.status != OppStatus.QUALIFIED.value:
            return self._finish(AgentResult(False, error=f"opportunity not qualified ({opp.status})"))

        service = infer_service(opp.title, opp.description, opp.skills or [])
        amount = opp.budget_cents or 150_000
        # Price at 85% of stated range when present — board convention from existing bidder.
        bid = int(amount * 0.85)

        proposal = {
            "version": opp.proposal_version + 1,
            "service": service,
            "bid_cents": bid,
            "currency": opp.budget_currency or "usd",
            "summary": (
                f"We will deliver a focused {service.replace('_', ' ')} for «{opp.title}», "
                f"scoped to the acceptance criteria below, with a reviewable preview before handoff."
            ),
            "approach": [
                "Confirm brief and acceptance criteria in writing",
                "Build in an isolated workspace with tests appropriate to the work",
                "Independent review before client delivery",
                "Provide a preview artifact and change log",
            ],
            "credentials_used": list(self.VERIFIED_CREDENTIALS),
            "exclusions": [
                "No fabricated case studies or fake reviews",
                "No work outside supported services",
                "No CAPTCHA/identity/access bypass",
            ],
            "estimated_days": 5,
        }
        self.store.update_opportunity(
            opportunity_id,
            proposal=proposal,
            proposal_version=proposal["version"],
        )
        self.store.advance(opportunity_id, OppStatus.PROPOSAL_DRAFTED)

        approval = self.store.create_approval(
            kind="proposal",
            entity_type="opportunity",
            entity_id=opportunity_id,
            summary=f"Approve proposal for: {opp.title}",
            body=json.dumps(proposal, indent=2),
            amount_cents=bid,
        )
        self.store.update_opportunity(opportunity_id, approval_id=approval["id"])
        self.store.advance(opportunity_id, OppStatus.AWAITING_APPROVAL)
        self._charge(5, tokens=800)

        # Standing auth check — submit always needs approval today.
        decision = self.auth.check_external_action(
            platform=opp.source,
            service=service,
            price_cents=bid,
            kind="submit_proposal",
        )
        return self._finish(
            AgentResult(
                True,
                {
                    "proposal": proposal,
                    "approval_id": approval["id"],
                    "auth": {"allowed": decision.allowed, "requires_approval": decision.requires_approval, "reason": decision.reason},
                },
            )
        )


class DeliveryPlanner(BaseFirmAgent):
    role = AgentRole.PLANNER
    name = "delivery_planner"

    def run(self, opportunity_id: str, **_) -> AgentResult:
        self._begin(deliverable=f"plan {opportunity_id}")
        self.use_tool("create_milestones")
        self.use_tool("define_acceptance")
        opp = self.store.get_opportunity(opportunity_id)
        if opp.status != OppStatus.WON.value:
            return self._finish(AgentResult(False, error=f"expected won, got {opp.status}"))

        service = (opp.proposal or {}).get("service") or infer_service(
            opp.title, opp.description, opp.skills or []
        )
        acceptance = [
            "Preview artifact loads without errors",
            "Matches agreed brief and brand signals",
            "Acceptance criteria checklist completed",
            "Independent review passed",
        ]
        if service == "workflow_automation":
            acceptance.append("Each automation step has a named trigger and output")
        elif service in ("website", "landing_page"):
            acceptance.append("Mobile and desktop layouts render correctly")

        project = self.store.create_project(
            opportunity_id=opportunity_id,
            brief=opp.scope or opp.description or opp.title,
            acceptance=acceptance,
            workspace_path="",  # set after we know project id
        )
        ws = DeliveryWorkspace(self.workdir / "workspaces", project["id"])
        self.store.conn.execute(
            "UPDATE projects SET workspace_path=? WHERE id=?",
            (str(ws.path), project["id"]),
        )
        self.store.conn.commit()
        project = self.store.get_project(project["id"])

        milestones = [
            ("Brief lock", "Owner and client confirm scope"),
            ("Build", "Deliverable produced in workspace"),
            ("Review", "Independent reviewer signs off"),
            ("Handoff", "Preview delivered and accepted"),
        ]
        for i, (title, acc) in enumerate(milestones):
            self.store.add_milestone(project["id"], title, acc, sort_order=i)

        self.store.advance(opportunity_id, OppStatus.DELIVERY)
        self._charge(3)
        return self._finish(
            AgentResult(True, {"project_id": project["id"], "acceptance": acceptance, "service": service})
        )


class EngineeringWorker(BaseFirmAgent):
    role = AgentRole.ENGINEER
    name = "engineering"

    def run(self, project_id: str, **_) -> AgentResult:
        self._begin(deliverable=f"build {project_id}")
        self.use_tool("write_workspace")
        self.use_tool("run_tests")
        self.use_tool("build_preview")
        project = self.store.get_project(project_id)
        opp = self.store.get_opportunity(project["opportunity_id"])
        service = (opp.proposal or {}).get("service") or "landing_page"
        ws = DeliveryWorkspace(self.workdir / "workspaces", project_id)

        if service in ("website", "landing_page"):
            brand = _extract_brand(opp.title)
            preview = ws.build_landing_page(
                brand=brand,
                headline="Ship the page your buyers actually need",
                support="A focused landing page built to your brief, reviewed before handoff.",
                cta="Request the preview",
                brief=project["brief"],
            )
        else:
            preview = ws.build_workflow_doc(
                title=opp.title,
                steps=[
                    "Trigger on approved inbound event",
                    "Validate payload and reject unsafe inputs",
                    "Execute approved automation step",
                    "Notify owner with audit trail",
                ],
                brief=project["brief"],
            )

        test_result = ws.run_tests()
        self.store.update_project(project_id, preview_path=str(preview), status="built")
        self._charge(10, tokens=1200)
        if not test_result.get("ok"):
            return self._finish(
                AgentResult(False, {"preview": str(preview), "tests": test_result}, error="tests failed")
            )
        return self._finish(AgentResult(True, {"preview": str(preview), "tests": test_result, "service": service}))


class IndependentReviewer(BaseFirmAgent):
    role = AgentRole.REVIEWER
    name = "independent_reviewer"

    def run(self, project_id: str, **_) -> AgentResult:
        self._begin(deliverable=f"review {project_id}")
        self.use_tool("review_deliverable")
        project = self.store.get_project(project_id)
        opp = self.store.get_opportunity(project["opportunity_id"])
        issues = []
        if not project.get("preview_path"):
            issues.append("no preview artifact")
        else:
            from pathlib import Path
            p = Path(project["preview_path"])
            if not p.exists():
                issues.append("preview path missing on disk")
            elif p.stat().st_size < 50:
                issues.append("preview artifact too small")

        # Prompt-injection / secret leakage checks on brief.
        brief = project.get("brief") or ""
        if any(x in brief.lower() for x in ("api_key", "sk-live", "password=")):
            issues.append("brief appears to contain secrets — do not propagate")

        passed = not issues
        if opp.status == OppStatus.DELIVERY.value:
            self.store.advance(project["opportunity_id"], OppStatus.REVIEW)
        if passed:
            self.store.advance(project["opportunity_id"], OppStatus.ACCEPTED)
            self.store.update_project(project_id, status="accepted", accepted_at=time.time())
        else:
            self.use_tool("request_revision")
            self.store.advance(project["opportunity_id"], OppStatus.DELIVERY)
            self.store.update_project(project_id, status="revision_required")

        self._charge(4)
        return self._finish(
            AgentResult(
                True,
                {
                    "passed": passed,
                    "issues": issues,
                    "acceptance": project.get("acceptance", []),
                },
            )
        )


class FinanceAgent(BaseFirmAgent):
    role = AgentRole.FINANCE
    name = "finance"

    def run(self, project_id: str, provider=None, customer_email: str = "client@example.com", **_) -> AgentResult:
        self._begin(deliverable=f"invoice {project_id}")
        self.use_tool("create_invoice")
        project = self.store.get_project(project_id)
        opp = self.store.get_opportunity(project["opportunity_id"])
        if opp.status != OppStatus.ACCEPTED.value:
            return self._finish(AgentResult(False, error=f"expected accepted, got {opp.status}"))

        amount = (opp.proposal or {}).get("bid_cents") or opp.budget_cents or 150_000
        currency = (opp.proposal or {}).get("currency") or opp.budget_currency or "usd"

        approval = self.store.create_approval(
            kind="invoice",
            entity_type="project",
            entity_id=project_id,
            summary=f"Issue invoice for {opp.title}",
            body=f"Amount: {amount} {currency}",
            amount_cents=amount,
        )
        # Finance records the intent; settlement only after provider confirmation.
        if provider is None:
            from .. import config as earner_config
            from ..payments import SandboxProvider, build_provider
            cfg = earner_config.load()
            if cfg.is_live:
                provider = build_provider(cfg)
                if getattr(provider, "name", "") == "sandbox":
                    return self._finish(
                        AgentResult(False, error="live mode requires Stripe — sandbox refused")
                    )
            else:
                return self._finish(
                    AgentResult(
                        False,
                        error=(
                            "Refusing sandbox invoice. Set EARNER_MODE=live and "
                            "STRIPE_API_KEY to issue real invoices."
                        ),
                    )
                )

        handle = provider.create_invoice(
            customer_email=customer_email,
            description=f"{opp.title} — firm delivery",
            amount_cents=amount,
            currency=currency,
            metadata={"project_id": project_id, "opportunity_id": opp.id},
        )
        inv = self.store.record_firm_invoice(
            project_id=project_id,
            provider=handle.provider,
            provider_ref=handle.provider_ref,
            amount_cents=handle.amount_cents,
            currency=handle.currency,
            lifecycle="issued",
            url=handle.url,
            simulated=handle.provider == "sandbox",
        )
        self.store.set_invoice_lifecycle(inv["id"], "pending")
        self.store.advance(opp.id, OppStatus.INVOICED)
        self._charge(2)
        return self._finish(
            AgentResult(
                True,
                {
                    "invoice_id": inv["id"],
                    "provider_ref": handle.provider_ref,
                    "approval_id": approval["id"],
                    "amount_cents": amount,
                    "simulated": handle.provider == "sandbox",
                    "lifecycle": "pending",
                },
            )
        )

    def check_settlement(self, invoice_id: str, provider) -> AgentResult:
        self._begin(deliverable=f"settle {invoice_id}")
        self.use_tool("check_settlement")
        inv = next((i for i in self.store.list_invoices() if i["id"] == invoice_id), None)
        if not inv:
            return self._finish(AgentResult(False, error="invoice not found"))
        if inv.get("lifecycle") == "settled":
            return self._finish(
                AgentResult(True, {"settled": False, "already_recorded": True, "lifecycle": "settled"})
            )
        settlement = provider.fetch_settlement(inv["provider_ref"])
        if settlement is None:
            return self._finish(AgentResult(True, {"settled": False, "lifecycle": inv["lifecycle"]}))
        if inv.get("lifecycle") != "provider_confirmed":
            self.store.set_invoice_lifecycle(invoice_id, "provider_confirmed")
        applied = self.store.confirm_payment(
            invoice_id, settlement.event_id, settlement.amount_cents, settlement.currency
        )
        # Find opportunity via project
        project = self.store.get_project(inv["project_id"])
        if applied:
            opp = self.store.get_opportunity(project["opportunity_id"])
            if opp.status == OppStatus.INVOICED.value:
                self.store.advance(project["opportunity_id"], OppStatus.PAID)
        elif inv.get("lifecycle") != "settled":
            # Payment row already existed — ensure lifecycle matches reality.
            self.store.set_invoice_lifecycle(invoice_id, "settled")
        self._charge(1)
        return self._finish(
            AgentResult(
                True,
                {
                    "settled": applied,
                    "already_recorded": not applied,
                    "amount_cents": settlement.amount_cents,
                    "simulated": bool(inv.get("simulated")),
                },
            )
        )


class ExperimentAnalyst(BaseFirmAgent):
    role = AgentRole.EXPERIMENT
    name = "experiment_analyst"

    def run(self, **_) -> AgentResult:
        self._begin(deliverable="pipeline experiment proposal")
        self.use_tool("measure_conversion")
        self.use_tool("propose_experiment")
        opps = self.store.list_opportunities()
        by_status: dict[str, int] = {}
        for o in opps:
            by_status[o.status] = by_status.get(o.status, 0) + 1

        bottleneck = _identify_bottleneck(by_status, opps)
        hypothesis, metric = _experiment_for(bottleneck)
        exp = self.store.create_experiment(
            hypothesis=hypothesis,
            metric=metric,
            bottleneck=bottleneck,
            expires_at=time.time() + 3600 * 6,
        )
        self._charge(3)
        return self._finish(
            AgentResult(
                True,
                {
                    "bottleneck": bottleneck,
                    "by_status": by_status,
                    "experiment_id": exp["id"],
                    "hypothesis": hypothesis,
                    "metric": metric,
                },
            )
        )


def _extract_brand(title: str) -> str:
    # Use first meaningful chunk as brand signal for demos.
    parts = [p.strip() for p in title.replace("—", "-").split("-") if p.strip()]
    return parts[0][:48] if parts else "Studio"


def _identify_bottleneck(by_status: dict[str, int], opps: list) -> str:
    if not opps:
        return "no_opportunities"
    if by_status.get("awaiting_approval", 0) > 0:
        return "approvals_pending"
    if by_status.get("proposal_drafted", 0) > by_status.get("submitted", 0):
        return "proposal_submission"
    if by_status.get("submitted", 0) > 0 and by_status.get("replied", 0) == 0 and by_status.get("won", 0) == 0:
        return "buyer_response_time"
    if by_status.get("won", 0) > by_status.get("accepted", 0) + by_status.get("paid", 0):
        return "delivery_readiness"
    if by_status.get("invoiced", 0) > by_status.get("paid", 0):
        return "payment_settlement"
    if by_status.get("qualified", 0) == 0 and by_status.get("discovered", 0) > 0:
        return "opportunity_quality"
    if by_status.get("discovered", 0) == len(opps):
        return "qualification"
    return "top_of_funnel"


def _experiment_for(bottleneck: str) -> tuple[str, str]:
    mapping = {
        "no_opportunities": (
            "Import 5 labeled sample + live RSS opportunities daily and measure fit≥0.35 rate",
            "qualified_per_day",
        ),
        "approvals_pending": (
            "Batch owner approval review twice daily; measure median approval latency",
            "median_approval_hours",
        ),
        "proposal_submission": (
            "Improve proposal specificity to acceptance criteria; measure reply rate",
            "reply_rate",
        ),
        "buyer_response_time": (
            "Distinguish response delay from loss; follow up once at 72h; measure reply lift",
            "reply_rate",
        ),
        "delivery_readiness": (
            "Pre-build preview templates for landing pages; measure time-to-preview",
            "hours_to_preview",
        ),
        "payment_settlement": (
            "Add provider settlement poll every 15m; measure days-to-cash",
            "days_to_settlement",
        ),
        "opportunity_quality": (
            "Tighten core-skill filter; measure qualified/discovered ratio",
            "qualification_rate",
        ),
        "qualification": (
            "Run qualification on all discovered opps within 15 minutes",
            "time_to_qualify_minutes",
        ),
        "top_of_funnel": (
            "Increase fresh-board sweeps; measure opportunities under 60 minutes old",
            "fresh_opps_per_day",
        ),
    }
    return mapping.get(bottleneck, mapping["top_of_funnel"])

"""Pipeline states, agent roles, and capability bounds."""

from __future__ import annotations

from enum import Enum


class OppStatus(str, Enum):
    DISCOVERED = "discovered"
    QUALIFIED = "qualified"
    PROPOSAL_DRAFTED = "proposal_drafted"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"  # owner approved; not yet externally submitted
    SUBMITTED = "submitted"
    REPLIED = "replied"
    WON = "won"  # signed contract / accepted SOW — requires evidence
    LOST = "lost"
    DELIVERY = "delivery"
    REVIEW = "review"
    ACCEPTED = "accepted"
    INVOICED = "invoiced"
    PAID = "paid"
    DISPUTED = "disputed"
    REJECTED = "rejected"
    DISQUALIFIED = "disqualified"
    UNSUBSCRIBED = "unsubscribed"
    STALLED = "stalled"


# Legal forward transitions only. advance() refuses anything else.
TRANSITIONS: dict[OppStatus, frozenset[OppStatus]] = {
    OppStatus.DISCOVERED: frozenset({OppStatus.QUALIFIED, OppStatus.REJECTED, OppStatus.DISQUALIFIED}),
    OppStatus.QUALIFIED: frozenset({OppStatus.PROPOSAL_DRAFTED, OppStatus.REJECTED, OppStatus.DISQUALIFIED}),
    OppStatus.PROPOSAL_DRAFTED: frozenset({OppStatus.AWAITING_APPROVAL, OppStatus.REJECTED}),
    OppStatus.AWAITING_APPROVAL: frozenset(
        {OppStatus.APPROVED, OppStatus.REJECTED, OppStatus.PROPOSAL_DRAFTED}
    ),
    OppStatus.APPROVED: frozenset(
        {OppStatus.SUBMITTED, OppStatus.REJECTED, OppStatus.STALLED}
    ),
    OppStatus.SUBMITTED: frozenset(
        {OppStatus.REPLIED, OppStatus.LOST, OppStatus.STALLED, OppStatus.UNSUBSCRIBED}
    ),
    OppStatus.REPLIED: frozenset({OppStatus.WON, OppStatus.LOST, OppStatus.STALLED}),
    OppStatus.WON: frozenset({OppStatus.DELIVERY}),
    OppStatus.DELIVERY: frozenset({OppStatus.REVIEW}),
    OppStatus.REVIEW: frozenset({OppStatus.ACCEPTED, OppStatus.DELIVERY}),
    OppStatus.ACCEPTED: frozenset({OppStatus.INVOICED}),
    OppStatus.INVOICED: frozenset({OppStatus.PAID, OppStatus.DISPUTED}),
    OppStatus.PAID: frozenset(),
    OppStatus.DISPUTED: frozenset({OppStatus.PAID, OppStatus.LOST}),
    OppStatus.LOST: frozenset(),
    OppStatus.REJECTED: frozenset(),
    OppStatus.DISQUALIFIED: frozenset(),
    OppStatus.UNSUBSCRIBED: frozenset(),
    OppStatus.STALLED: frozenset({OppStatus.SUBMITTED, OppStatus.REPLIED, OppStatus.LOST}),
}


class AgentRole(str, Enum):
    COORDINATOR = "coordinator"
    RESEARCHER = "opportunity_researcher"
    QUALIFIER = "qualification"
    PROPOSAL = "proposal"
    PLANNER = "delivery_planner"
    ENGINEER = "engineering"
    REVIEWER = "independent_reviewer"
    FINANCE = "finance"
    EXPERIMENT = "experiment_analyst"
    CO_AGENT = "co_agent"


# Tools each role may invoke. Agents cannot expand this set themselves.
ROLE_PERMISSIONS: dict[AgentRole, frozenset[str]] = {
    AgentRole.COORDINATOR: frozenset(
        {"schedule_task", "cancel_task", "pause_firm", "spawn_co_agent", "merge_agent"}
    ),
    AgentRole.RESEARCHER: frozenset({"read_feed", "import_opportunity", "score_freshness"}),
    AgentRole.QUALIFIER: frozenset({"score_fit", "flag_suspicious", "check_capability"}),
    AgentRole.PROPOSAL: frozenset({"draft_proposal", "read_credentials"}),
    AgentRole.PLANNER: frozenset({"create_milestones", "define_acceptance"}),
    AgentRole.ENGINEER: frozenset({"write_workspace", "run_tests", "build_preview"}),
    AgentRole.REVIEWER: frozenset({"review_deliverable", "request_revision"}),
    AgentRole.FINANCE: frozenset({"create_invoice", "check_settlement", "report_costs"}),
    AgentRole.EXPERIMENT: frozenset({"propose_experiment", "measure_conversion"}),
    AgentRole.CO_AGENT: frozenset({"specialist_work"}),
}


# Work we refuse regardless of score.
FORBIDDEN_PATTERNS = (
    "fake review",
    "fabricated credential",
    "account rental",
    "captcha bypass",
    "identity verification bypass",
    "buy followers",
    "click farm",
    "guaranteed ranking",
    "model training at scale",
    "gpu cluster",
    "train llm",
)


class InvoiceLifecycle(str, Enum):
    ISSUED = "issued"
    PENDING = "pending"
    PROVIDER_CONFIRMED = "provider_confirmed"
    SETTLED = "settled"
    DISPUTED = "disputed"
    VOID = "void"

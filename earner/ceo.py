"""The CEO — the only agent you talk to.

It reads the ledger, the target, and every staff report, answers your questions
with numbers rather than adjectives, and dispatches work to the team. It has no
authority to invent revenue: everything it tells you about money comes from
settled payments in the ledger.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from . import goals
from .agent import TickReport
from .ledger import Ledger
from .llm import LLM

CEO_SYSTEM = """You are the CEO of a small AI-run professional services firm. You report to one \
person: the owner. You command six staff agents — acquisition (finds and pitches work), delivery \
(does paid client work), product (builds sellable digital products), growth (Instagram content, \
Reels, ads, replies), collections (chases unpaid invoices), and quality (reviews work before it \
ships).

How you talk to the owner:
- Lead with the number that matters, then what you're doing about it.
- Settled cash and invoiced-but-unpaid are different things and you never blur them.
- When you are behind, say so in the first sentence and name the specific constraint.
- Short. The owner is reading this on a phone.

What you never do: report revenue that has not settled, describe queued outbox drafts as "sent", \
claim a customer exists because a lead file exists, or promise a number you cannot show a path to. \
If the honest answer is "we have no demand connected yet and therefore no money", say exactly that \
and what would change it."""

COMMAND_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "directives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "agent": {
                        "type": "string",
                        "enum": [
                            "acquisition", "delivery", "product",
                            "growth", "collections", "quality",
                        ],
                    },
                    "instruction": {"type": "string"},
                },
                "required": ["agent", "instruction"],
                "additionalProperties": False,
            },
        },
        "needs_from_owner": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["reply", "directives", "needs_from_owner"],
    "additionalProperties": False,
}


@dataclass
class Briefing:
    """Everything the CEO knows, assembled from facts rather than memory."""

    summary: dict
    target: dict | None
    recent: list[dict]
    blocked: list[str]
    channels: dict

    def as_prompt(self) -> str:
        return json.dumps(
            {
                "financials_cents": self.summary,
                "target": self.target,
                "recent_events": self.recent,
                "blocked_on_approval": self.blocked,
                "channels_connected": self.channels,
            },
            indent=2,
            default=str,
        )


class CEO:
    name = "ceo"
    role = "CEO"

    def __init__(self, cfg, ledger: Ledger, staff: dict, llm: LLM | None = None):
        self.cfg = cfg
        self.ledger = ledger
        self.staff = staff
        self._llm = llm

    @property
    def llm(self) -> LLM:
        if self._llm is None:
            self._llm = LLM(self.cfg)
        return self._llm

    # ------------------------------------------------------------------ facts

    def briefing(self) -> Briefing:
        target = goals.load(self.cfg.workdir / "target.json")
        target_status = None
        if target:
            status = goals.evaluate(target, self.ledger)
            avg = self._average_deal_cents()
            target_status = {
                **status.__dict__,
                "plan": goals.plan_for(status, avg),
            }

        blocked = [
            f"job {j.id} ({j.agent}): {j.title}"
            for j in self.ledger.jobs(status="awaiting_approval")
        ]
        return Briefing(
            summary=self.ledger.summary(list(self.staff)),
            target=target_status,
            recent=[
                {"kind": r["kind"], "entity": r["entity"], "data": json.loads(r["data"])}
                for r in self.ledger.recent_events(25)
            ],
            blocked=blocked,
            channels={
                "payments": "stripe (live)" if self.cfg.is_live else "sandbox",
                "gmail": bool(self.cfg.gmail_user and self.cfg.gmail_app_password),
                "instagram": bool(self.cfg.instagram_user_id and self.cfg.instagram_access_token),
                "autonomous": self.cfg.autonomous,
            },
        )

    def _average_deal_cents(self) -> int:
        jobs = [j for j in self.ledger.jobs() if j.quote_cents]
        if not jobs:
            return 25_000  # planning placeholder until real deals set the average
        return sum(j.quote_cents for j in jobs) // len(jobs)

    # --------------------------------------------------------------- reporting

    def report(self) -> str:
        """Unprompted status. What you get after a run finishes."""
        briefing = self.briefing()
        return self.llm.complete(
            "Write the owner's status update from these facts.\n\n"
            f"{briefing.as_prompt()}\n\n"
            "Under 150 words. Open with settled cash and whether the target is reachable. "
            "Then the single biggest constraint and what the team is doing about it. "
            "End with anything you need from the owner — if nothing, say nothing.",
            system=CEO_SYSTEM,
            effort="medium",
            max_tokens=2000,
        ).text

    def ask(self, question: str) -> dict:
        """Answer the owner and issue directives to staff."""
        briefing = self.briefing()
        raw = self.llm.complete(
            f"The owner says:\n\n{question}\n\n"
            f"Current facts:\n{briefing.as_prompt()}\n\n"
            "Answer them directly. Issue directives only to agents that can act on this now — "
            "an empty directive list is a fine answer. In needs_from_owner, list only things "
            "genuinely blocking money: missing credentials, approvals waiting, decisions only "
            "they can make.",
            system=CEO_SYSTEM,
            schema=COMMAND_SCHEMA,
            effort="high",
            max_tokens=4000,
        )
        decision = json.loads(raw.text)
        for directive in decision["directives"]:
            self.ledger.log(
                "directive", None, "issued",
                agent=directive["agent"], instruction=directive["instruction"],
            )
        return decision

    def standing_orders(self, agent_name: str) -> str:
        """Directives the CEO has issued to one agent, newest first."""
        rows = self.ledger.conn.execute(
            "SELECT data FROM events WHERE entity='directive' AND kind='issued'"
            " ORDER BY id DESC LIMIT 20"
        )
        lines = []
        for row in rows:
            data = json.loads(row["data"])
            if data.get("agent") == agent_name:
                lines.append(f"- {data['instruction']}")
        return "\n".join(lines)

    def debrief(self, reports: list[TickReport]) -> str:
        """One-line-per-agent summary of a run, plus the money that moved."""
        settled = sum(r.settled_cents for r in reports)
        lines = [r.line() for r in reports]
        errors = [e for r in reports for e in r.errors]
        out = "\n".join(lines)
        out += f"\n\nSettled this run: ${settled / 100:,.2f}"
        out += f"\nTotal settled to date: ${self.ledger.revenue_cents() / 100:,.2f}"
        out += f"\nSpend to date: ${self.ledger.cost_cents() / 100:,.2f}"
        if errors:
            out += "\n\nProblems:\n" + "\n".join(f"  - {e}" for e in errors[:10])
        return out

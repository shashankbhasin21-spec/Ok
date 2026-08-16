"""Human approval gates.

Anything that reaches a customer or a bank account goes through here first:
quotes, invoices, outbound messages, deliverables. The default gate holds the
job rather than guessing, so an unattended run stalls safely instead of
sending something wrong to a real person.
"""

from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass

APPROVED = "approved"
REJECTED = "rejected"
HELD = "held"


@dataclass
class Action:
    kind: str  # "quote" | "invoice" | "message" | "deliverable"
    summary: str
    body: str = ""
    amount_cents: int | None = None
    recipient: str | None = None

    def render(self) -> str:
        lines = [f"[{self.kind}] {self.summary}"]
        if self.recipient:
            lines.append(f"  to:     {self.recipient}")
        if self.amount_cents is not None:
            lines.append(f"  amount: ${self.amount_cents / 100:,.2f}")
        if self.body:
            preview = textwrap.shorten(self.body.replace("\n", " "), 400, placeholder=" …")
            lines.append(f"  body:   {preview}")
        return "\n".join(lines)


@dataclass
class Decision:
    result: str
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.result == APPROVED


class HoldGate:
    """Default. Approves nothing; parks the job for a human to review."""

    def review(self, action: Action) -> Decision:
        return Decision(HELD, "no approver configured — job parked for review")


class CLIGate:
    """Prompts on a terminal. Falls back to holding when there's no TTY."""

    def review(self, action: Action) -> Decision:
        if not sys.stdin.isatty():
            return Decision(HELD, "no interactive terminal available")
        print("\n" + action.render())
        try:
            answer = input("approve? [y/N] ").strip().lower()
        except EOFError:
            return Decision(HELD, "input closed")
        if answer in ("y", "yes"):
            return Decision(APPROVED, "approved interactively")
        return Decision(REJECTED, "rejected interactively")


class AutoGate:
    """Approves everything. Only sensible for sandbox runs and tests.

    ``allow_live`` must be set explicitly to use this against real money, so
    nobody gets there by leaving a flag on from a dry run.
    """

    def __init__(self, *, allow_live: bool = False, is_live: bool = False):
        if is_live and not allow_live:
            raise ValueError(
                "AutoGate refuses to run in live mode without allow_live=True — "
                "auto-approving real invoices and customer messages needs an explicit decision"
            )

    def review(self, action: Action) -> Decision:
        return Decision(APPROVED, "auto-approved")


def build_gate(name: str, *, is_live: bool = False) -> HoldGate | CLIGate | AutoGate:
    gates = {
        "hold": lambda: HoldGate(),
        "cli": lambda: CLIGate(),
        "auto": lambda: AutoGate(allow_live=is_live is False or _env_allows_live(), is_live=is_live),
    }
    if name not in gates:
        raise ValueError(f"unknown approval gate {name!r}; choose from {sorted(gates)}")
    return gates[name]()


def _env_allows_live() -> bool:
    import os

    return os.environ.get("EARNER_ALLOW_LIVE_AUTOAPPROVE") == "1"

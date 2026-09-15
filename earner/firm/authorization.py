"""Standing authorization and action gates.

External actions require either an explicit approval or a matching standing
authorization within platform / service / price / volume / spend bounds.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import AgentRole, ROLE_PERMISSIONS
from .store import FirmStore


@dataclass
class AuthDecision:
    allowed: bool
    requires_approval: bool
    reason: str


class AuthorizationError(PermissionError):
    pass


class Authorization:
    def __init__(self, store: FirmStore):
        self.store = store

    def check_tool(self, role: AgentRole | str, tool: str) -> None:
        role = AgentRole(role) if isinstance(role, str) else role
        allowed = ROLE_PERMISSIONS.get(role, frozenset())
        if tool not in allowed:
            raise AuthorizationError(
                f"role {role.value} cannot use tool {tool!r}; "
                f"agents cannot grant themselves new permissions"
            )

    def check_external_action(
        self,
        *,
        platform: str,
        service: str,
        price_cents: int,
        kind: str,
    ) -> AuthDecision:
        """Decide whether an external action can proceed under standing auth.

        New contracts, spending commitments, and ungranted access always need
        approval. Standing auth only covers bounded, previously permitted actions.
        """
        if self.store.is_paused():
            return AuthDecision(False, False, "firm is paused")

        breaker = int(self.store.get_meta("cost_circuit_breaker_cents", "5000"))
        if self.store.total_agent_cost_cents() >= breaker:
            return AuthDecision(
                False, False, f"cost circuit breaker tripped at ${breaker / 100:.2f}"
            )

        # New contracts and beneficiary changes always need human approval.
        if kind in ("new_contract", "change_payout", "grant_access", "submit_proposal"):
            return AuthDecision(
                False,
                True,
                f"{kind} requires explicit owner approval",
            )

        auths = [
            a
            for a in self.store.list_standing_auth()
            if a["platform"] == platform and a["service"] == service and a["enabled"]
        ]
        if not auths:
            return AuthDecision(
                False,
                True,
                f"no standing authorization for {platform}/{service}",
            )

        auth = auths[0]
        if price_cents > auth["max_price_cents"]:
            return AuthDecision(
                False,
                True,
                f"price ${price_cents / 100:.2f} exceeds standing max "
                f"${auth['max_price_cents'] / 100:.2f}",
            )

        usage = self.store.auth_usage_today(platform, service)
        if usage["volume"] >= auth["daily_volume"]:
            return AuthDecision(
                False,
                True,
                f"daily volume limit ({auth['daily_volume']}) reached for {platform}/{service}",
            )
        if usage["spend_cents"] + price_cents > auth["daily_spend_cents"]:
            return AuthDecision(
                False,
                True,
                f"daily spend limit (${auth['daily_spend_cents'] / 100:.2f}) would be exceeded",
            )

        return AuthDecision(True, False, "within standing authorization")

    def assert_can_spawn(self) -> None:
        max_c = int(self.store.get_meta("max_concurrent_agents", "4"))
        active = self.store.active_agent_count()
        if active >= max_c:
            raise AuthorizationError(
                f"max concurrent agents ({max_c}) reached; "
                f"$1,000/hour is not permission to spawn unbounded agents"
            )
        if self.store.is_paused():
            raise AuthorizationError("firm is paused")

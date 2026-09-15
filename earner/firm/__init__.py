"""Multi-agent firm platform — opportunity pipeline, orchestration, dashboard API."""

from __future__ import annotations

# Financial targets (USD). Targets only — not spend or spawn authorizations.
ASPIRATIONAL_RATE_CENTS_PER_HOUR = 100_000  # $1,000 / hour
MILESTONE_CENTS = 2_000_000  # $20,000 cumulative settled cash

SUPPORTED_SERVICES = (
    "website",
    "landing_page",
    "workflow_automation",
    "software_fix",
)

DEFAULT_MAX_CONCURRENT_AGENTS = 4
HOURLY_REVIEW_SECONDS = 3600

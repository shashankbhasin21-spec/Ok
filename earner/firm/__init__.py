"""Multi-agent firm platform — opportunity pipeline, orchestration, dashboard API."""

from __future__ import annotations

# Financial targets (USD). Targets only — not spend or spawn authorizations.
ASPIRATIONAL_RATE_CENTS_PER_HOUR = 200_000  # $2,000 / hour per-agent target
MILESTONE_CENTS = 2_000_000  # $20,000 cumulative settled cash
MONTHLY_CASH_TARGET_CENTS = 30_000_000  # $300,000 per calendar month (UTC)
BOOKINGS_TARGET_CENTS = 30_000_000  # $300,000 signed bookings by 2026-09-30

SUPPORTED_SERVICES = (
    "website",
    "landing_page",
    "workflow_automation",
    "software_fix",
)

DEFAULT_MAX_CONCURRENT_AGENTS = 4
HOURLY_REVIEW_SECONDS = 3600

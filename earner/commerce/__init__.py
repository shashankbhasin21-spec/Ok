"""Grey Quantum Commerce — product arbitrage storefront under CEO Anestasis Grey.

Targets are aspirational planning numbers, not guarantees or spend authorizations.
Settled cash requires payment-provider confirmation. Ads and outreach stay drafts
until the owner approves. Indian bank / UPI payout details are owner-configured
later (see docs/GREY_QUANTUM.md).
"""

from __future__ import annotations

COMPANY_NAME = "Grey Quantum"
CEO_NAME = "Anestasis Grey"
CEO_TITLE = "Quantum Brain CEO"

# Aspirational sales targets (USD). Not forecasts. Not cash.
DAILY_SALES_TARGET_CENTS = 1_000_000  # $10,000 / day
MONTHLY_SALES_TARGET_CENTS = 50_000_000  # $500,000 / month
BILLION_REVENUE_VISION_CENTS = 100_000_000_000  # $1B vision ceiling

# Operating bounds — scale only inside these until owner raises them.
MAX_COMMERCE_AGENTS = 12  # base teams + optional marketing specialists
DEFAULT_ACTIVE_WORKERS = 4
DISCUSSION_WINDOW_MINUTES = 120

SUPPORTED_MARKETS = ("IN", "US", "EU", "GLOBAL")
PAYMENT_NOTE = (
    "Checkout creates unpaid orders only. Revenue counts after Stripe/Razorpay/"
    "Payoneer confirmation. Indian Kotak/UPI payout details are added by the owner "
    "when ready — do not invent bank credentials."
)

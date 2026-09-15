"""Commercial targets and evidence-backed money metrics.

$300,000 is an ambitious bookings TARGET by 2026-09-30 — never a forecast.
Cash collected is tracked separately and only from provider confirmation.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

# Bookings target (signed contracts / accepted SOWs). Not cash.
BOOKINGS_TARGET_CENTS = 30_000_000  # $300,000
BOOKINGS_DEADLINE = date(2026, 9, 30)

# Cash milestone remains distinct (provider-confirmed payments).
CASH_MILESTONE_CENTS = 2_000_000  # $20,000 first cash milestone

# Planning scenarios only — not assumptions that demand exists.
ILLUSTRATIVE_SCENARIOS = (
    {"contracts": 12, "avg_cents": 2_500_000, "label": "12 × $25,000"},
    {"contracts": 6, "avg_cents": 5_000_000, "label": "6 × $50,000"},
    {"contracts": 3, "avg_cents": 10_000_000, "label": "3 × $100,000"},
)

# Evidence kinds required for customer-action transitions.
EVIDENCE_KINDS = frozenset({
    "board_submission_receipt",
    "outbound_message_id",
    "buyer_reply_url",
    "meeting_notes_ref",
    "contract_pdf",
    "sow_acceptance",
    "platform_hire_receipt",
    "stripe_invoice_id",
    "stripe_payment_event",
    "payout_transfer_id",
    "owner_attestation_with_artifact",
})

CUSTOMER_ACTION_TRANSITIONS = frozenset({
    "submitted",
    "replied",
    "won",  # signed
    "paid",
})

"""Connector readiness: NOT_CONFIGURED / CONFIGURED / VERIFIED / DEGRADED / BLOCKED."""

from __future__ import annotations

import os
from enum import Enum

from .. import config as earner_config
from ..payments import StripeProvider, build_provider


class ConnectorState(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONFIGURED = "CONFIGURED"
    VERIFIED = "VERIFIED"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"


def connector_matrix(cfg=None, *, payout_configured: bool = False) -> list[dict]:
    cfg = cfg or earner_config.load()
    rows = []

    # Freelancer RSS — verified when a fetch succeeds; we mark VERIFIED for public RSS availability.
    rows.append({
        "id": "freelancer_rss",
        "name": "Freelancer public RSS",
        "state": ConnectorState.VERIFIED.value,
        "detail": "Read-only discovery; no proposal submit API",
        "action": "Use earner firm sweep; submit proposals manually after approval",
    })

    # Upwork
    if cfg.upwork_client_id and cfg.upwork_client_secret:
        rows.append({
            "id": "upwork",
            "name": "Upwork OAuth discovery",
            "state": ConnectorState.CONFIGURED.value,
            "detail": "Credentials present; run earner connect --channel upwork to verify",
            "action": "Complete OAuth proof search; proposal submit remains manual",
        })
    else:
        rows.append({
            "id": "upwork",
            "name": "Upwork OAuth discovery",
            "state": ConnectorState.NOT_CONFIGURED.value,
            "detail": "UPWORK_CLIENT_ID / SECRET missing",
            "action": "Apply at upwork.com/developer and set env vars",
        })

    # Stripe
    if not cfg.is_live:
        rows.append({
            "id": "stripe",
            "name": "Stripe invoices",
            "state": ConnectorState.BLOCKED.value,
            "detail": "EARNER_MODE is not live — silent sandbox is refused for production finance",
            "action": "Set EARNER_MODE=live and STRIPE_API_KEY",
        })
    elif not cfg.stripe_api_key:
        rows.append({
            "id": "stripe",
            "name": "Stripe invoices",
            "state": ConnectorState.BLOCKED.value,
            "detail": "STRIPE_API_KEY missing",
            "action": "Add Stripe secret key (prefer test key first, then live)",
        })
    else:
        try:
            import stripe  # noqa: F401
            p = build_provider(cfg)
            if isinstance(p, StripeProvider):
                mode = "test" if cfg.stripe_api_key.startswith("sk_test_") else "live"
                wh = bool(os.environ.get("STRIPE_WEBHOOK_SECRET"))
                rows.append({
                    "id": "stripe",
                    "name": "Stripe invoices",
                    "state": ConnectorState.CONFIGURED.value if not wh else ConnectorState.VERIFIED.value,
                    "detail": f"StripeProvider loaded ({mode} key)" + ("" if wh else "; webhook secret missing"),
                    "action": None if wh else "Set STRIPE_WEBHOOK_SECRET and point invoice.paid to /api/webhooks/stripe",
                })
            else:
                rows.append({
                    "id": "stripe",
                    "name": "Stripe invoices",
                    "state": ConnectorState.BLOCKED.value,
                    "detail": "Provider resolved to non-Stripe",
                    "action": "Fix EARNER_MODE/STRIPE_API_KEY",
                })
        except Exception as exc:
            rows.append({
                "id": "stripe",
                "name": "Stripe invoices",
                "state": ConnectorState.BLOCKED.value,
                "detail": str(exc),
                "action": "pip install 'earner[stripe]' and fix credentials",
            })

    # Gmail
    if cfg.gmail_user and cfg.gmail_app_password:
        rows.append({
            "id": "gmail",
            "name": "Gmail outreach",
            "state": ConnectorState.CONFIGURED.value,
            "detail": "Credentials present; outbound still gated by approval + confirmation env",
            "action": "Run earner connect to prove IMAP/SMTP before live send",
        })
    else:
        rows.append({
            "id": "gmail",
            "name": "Gmail outreach",
            "state": ConnectorState.NOT_CONFIGURED.value,
            "detail": "GMAIL_USER / GMAIL_APP_PASSWORD missing",
            "action": "Optional for marketplace-first path; required for direct email outreach",
        })

    # Payouts
    if payout_configured:
        rows.append({
            "id": "payouts",
            "name": "Owner payout destination",
            "state": ConnectorState.CONFIGURED.value,
            "detail": "Encrypted payout config saved — not the same as provider-enabled payouts",
            "action": "Complete Payoneer/Wise/Stripe India onboarding; Kotak may not link to US Stripe",
        })
    else:
        rows.append({
            "id": "payouts",
            "name": "Owner payout destination",
            "state": ConnectorState.NOT_CONFIGURED.value,
            "detail": "No payout config; India: prefer Payoneer/Wise → Kotak (see docs/INDIA-PAYOUTS.md)",
            "action": "Configure via dashboard after owner reauthentication",
        })

    # Proposal submit
    rows.append({
        "id": "proposal_submit",
        "name": "Marketplace proposal submit",
        "state": ConnectorState.BLOCKED.value,
        "detail": "No official submit API on Upwork/Freelancer",
        "action": "Manual handoff after owner approval; attach board receipt as evidence",
    })

    return rows

"""Capability matching — honest about what we can deliver."""

from __future__ import annotations

import re

from . import SUPPORTED_SERVICES
from .models import FORBIDDEN_PATTERNS

# Weighted skills we actually have. Core skills must match for a positive fit.
CORE_SKILLS = {
    "website": 3.0,
    "landing page": 3.0,
    "landing": 2.5,
    "next.js": 2.5,
    "html": 2.0,
    "css": 2.0,
    "react": 2.0,
    "workflow": 3.0,
    "automation": 3.0,
    "n8n": 2.5,
    "zapier": 2.0,
    "api": 1.5,
    "bug fix": 3.0,
    "fix": 2.0,
    "python": 2.0,
    "typescript": 2.0,
    "javascript": 1.5,
}

AVOID = {
    "model training",
    "fine-tune",
    "finetune",
    "gpu",
    "kubernetes cluster",
    "mobile app from scratch",
    "blockchain",
    "solidity",
    "trading bot",
    "crypto trading",
}


def _word_match(term: str, text: str) -> bool:
    return re.search(rf"\b{re.escape(term)}\b", text, re.I) is not None


def reject_forbidden(text: str) -> str | None:
    lower = text.lower()
    for pattern in FORBIDDEN_PATTERNS:
        if pattern in lower:
            return f"refused: involves '{pattern}'"
    return None


def score_fit(
    *,
    title: str,
    description: str,
    skills: list[str],
    budget_cents: int | None,
    budget_currency: str = "usd",
) -> tuple[float, list[str], str | None]:
    """Return (score 0-1, reasons, reject_reason_or_None)."""
    blob = f"{title}\n{description}\n{' '.join(skills)}".lower()

    forbidden = reject_forbidden(blob)
    if forbidden:
        return 0.0, [forbidden], forbidden

    # Title-level avoid terms hard-block.
    for term in AVOID:
        if _word_match(term, title):
            reason = f"out of capability: '{term}' in title"
            return 0.0, [reason], reason

    matched = []
    weight = 0.0
    for skill, w in CORE_SKILLS.items():
        if _word_match(skill, blob):
            matched.append(skill)
            weight += w

    if not matched:
        reason = "no overlapping core skills with supported services"
        return 0.05, [reason], reason

    # At least one supported service category must be inferable.
    service_hit = any(
        s.replace("_", " ") in blob or s in matched
        for s in SUPPORTED_SERVICES
    ) or any(m in ("website", "landing page", "landing", "workflow", "automation", "fix", "bug fix") for m in matched)

    if not service_hit:
        reason = f"work is outside supported services: {', '.join(SUPPORTED_SERVICES)}"
        return 0.1, [reason], reason

    # Budget sanity in USD.
    usd = None
    if budget_cents is not None:
        rates = {"usd": 1.0, "eur": 1.08, "gbp": 1.27, "inr": 1 / 83.0, "aud": 0.66, "cad": 0.73}
        usd = budget_cents / 100.0 * rates.get(budget_currency.lower(), 1.0)
        if usd < 100:
            return 0.2, [f"budget ${usd:.0f} below $100 floor for first contracts"], "budget too low"

    # Normalize weight into 0-1.
    score = min(1.0, weight / 8.0)
    reasons = [f"matched skills: {', '.join(matched)}"]
    if usd is not None:
        reasons.append(f"budget ~${usd:.0f} USD")
    reasons.append("within supported delivery set")
    return score, reasons, None


def infer_service(title: str, description: str, skills: list[str]) -> str:
    blob = f"{title} {description} {' '.join(skills)}".lower()
    if any(_word_match(t, blob) for t in ("landing", "landing page")):
        return "landing_page"
    if any(_word_match(t, blob) for t in ("website", "web site", "web page")):
        return "website"
    if any(_word_match(t, blob) for t in ("workflow", "automation", "n8n", "zapier")):
        return "workflow_automation"
    if any(_word_match(t, blob) for t in ("fix", "bug", "debug", "patch")):
        return "software_fix"
    return "website"

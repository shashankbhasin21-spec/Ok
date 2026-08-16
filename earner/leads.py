"""Lead intake.

A lead is a real person or company that might pay you. This module's whole job
is refusing anything that isn't one — placeholder emails, example domains,
blank contacts — because a pipeline full of invented prospects produces
confident forecasts and zero cash.

Two ways in: type one by hand, or point it at a real public job posting and
let Claude read the posting into a structured lead.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.IGNORECASE)

# Domains that only ever appear in documentation and placeholder data.
FAKE_DOMAINS = {
    "example.com", "example.org", "example.net", "test.com", "email.com",
    "domain.com", "yourcompany.com", "company.com", "acme.com", "foo.com",
    "sample.com", "mycompany.com", "placeholder.com",
}
FAKE_LOCALPARTS = {"test", "testing", "example", "placeholder", "foo", "bar", "demo", "sample"}


class InvalidLead(ValueError):
    """The lead is not a real, contactable prospect."""


@dataclass
class Lead:
    company: str
    notes: str
    email: str = ""
    contact_name: str = ""
    website: str = ""
    source: str = "manual"
    service: str = ""
    evidence: str = ""  # why you believe they need this — the pitch hangs on it
    tags: list[str] = field(default_factory=list)

    @property
    def ref(self) -> str:
        key = (self.email or self.website or self.company).lower()
        return "lead-" + hashlib.sha256(key.encode()).hexdigest()[:12]


def validate(lead: Lead) -> Lead:
    """Reject anything that cannot actually be sold to."""
    if not lead.company.strip():
        raise InvalidLead("a lead needs a company or person name")
    if not lead.notes.strip() and not lead.evidence.strip():
        raise InvalidLead(
            "a lead needs notes or evidence — without a reason to contact them, "
            "acquisition can only write a generic pitch, which is worse than none"
        )

    if lead.email:
        email = lead.email.strip().lower()
        if not EMAIL_RE.match(email):
            raise InvalidLead(f"{lead.email!r} is not a valid email address")
        localpart, _, domain = email.partition("@")
        if domain in FAKE_DOMAINS:
            raise InvalidLead(
                f"{domain} is a placeholder domain — a pipeline of invented prospects "
                "forecasts revenue that cannot arrive"
            )
        if localpart in FAKE_LOCALPARTS:
            raise InvalidLead(f"{localpart}@ looks like placeholder data, not a real contact")
        lead.email = email
    elif not lead.website:
        raise InvalidLead("a lead needs an email or a website — otherwise nobody can be reached")

    return lead


def save(lead: Lead, inbox: Path) -> Path:
    """Write the lead where acquisition will find it. Idempotent per contact."""
    validate(lead)
    folder = Path(inbox) / "leads"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{lead.ref}.json"
    if path.exists():
        raise InvalidLead(f"already in the pipeline as {lead.ref}")
    path.write_text(json.dumps(asdict(lead), indent=2))
    return path


def load_all(inbox: Path) -> list[Lead]:
    folder = Path(inbox) / "leads"
    if not folder.exists():
        return []
    out = []
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        known = {f for f in Lead.__dataclass_fields__}
        out.append(Lead(**{k: v for k, v in data.items() if k in known}))
    return out


POSTING_SCHEMA = {
    "type": "object",
    "properties": {
        "is_real_opportunity": {"type": "boolean"},
        "reason": {"type": "string"},
        "company": {"type": "string"},
        "contact_name": {"type": "string"},
        "email": {"type": "string"},
        "website": {"type": "string"},
        "service_wanted": {"type": "string"},
        "evidence": {"type": "string"},
        "budget_hint": {"type": "string"},
    },
    "required": [
        "is_real_opportunity", "reason", "company", "contact_name",
        "email", "website", "service_wanted", "evidence", "budget_hint",
    ],
    "additionalProperties": False,
}


def from_posting(url: str, llm, inbox: Path) -> Lead:
    """Read a real public job posting into a lead.

    Claude fetches the page itself, so what lands in the pipeline comes from
    the posting rather than from a guess about it. Fields the posting does not
    state are left empty instead of being filled in plausibly.
    """
    raw = llm.complete(
        f"Read this job posting and extract the lead: {url}\n\n"
        "Fetch the page. Set is_real_opportunity=false if it is not a genuine open request "
        "for paid work — a closed listing, an aggregator index page, or an advert aimed at "
        "freelancers rather than a buyer.\n\n"
        "Leave any field the posting does not actually state as an empty string. Do not infer "
        "an email address, do not guess a company name from the URL, and do not invent a "
        "budget. 'evidence' must quote or closely paraphrase what the posting says they need.",
        system=(
            "You extract sales leads from public job postings. You are precise about the "
            "difference between what a posting states and what would be reasonable to assume, "
            "and you never fill a gap with a plausible invention."
        ),
        research=True,
        effort="medium",
        max_tokens=4000,
    )
    text = raw.text.strip()
    if "{" in text:
        text = text[text.index("{") : text.rindex("}") + 1]
    data = json.loads(text)

    if not data["is_real_opportunity"]:
        raise InvalidLead(f"not a live opportunity: {data['reason']}")

    lead = Lead(
        company=data["company"],
        contact_name=data["contact_name"],
        email=data["email"],
        website=data["website"] or url,
        notes=data["budget_hint"],
        evidence=data["evidence"],
        service=data["service_wanted"],
        source=f"posting:{url}",
    )
    save(lead, inbox)
    return lead

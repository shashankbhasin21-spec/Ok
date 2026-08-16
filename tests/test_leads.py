"""Lead intake refuses anything that cannot actually be sold to."""

from __future__ import annotations

import pytest

from earner.leads import InvalidLead, Lead, load_all, save, validate


def test_a_real_lead_is_accepted(cfg):
    lead = Lead(
        company="Northwind Logistics",
        email="ops@northwindlogistics.co.uk",
        notes="Quoted 3-day turnaround on freight quotes; wants it same-day.",
        evidence="Their careers page lists two open 'quote desk coordinator' roles.",
    )
    path = save(lead, cfg.inbox)
    assert path.exists()
    assert load_all(cfg.inbox)[0].company == "Northwind Logistics"


@pytest.mark.parametrize(
    "email",
    ["client@example.com", "test@realcompany.io", "hello@yourcompany.com", "not-an-email"],
)
def test_placeholder_contacts_are_refused(email):
    """Invented prospects forecast revenue that cannot arrive."""
    with pytest.raises(InvalidLead):
        validate(Lead(company="Some Co", email=email, notes="looks promising"))


def test_a_lead_with_no_reason_is_refused():
    with pytest.raises(InvalidLead, match="notes or evidence"):
        validate(Lead(company="Real Co", email="hi@realco.dev", notes="", evidence=""))


def test_a_lead_with_no_way_to_reach_them_is_refused():
    with pytest.raises(InvalidLead, match="email or a website"):
        validate(Lead(company="Real Co", notes="saw their job post"))


def test_the_same_contact_is_not_added_twice(cfg):
    lead = Lead(company="Northwind", email="ops@northwind.co.uk", notes="same-day quotes")
    save(lead, cfg.inbox)
    with pytest.raises(InvalidLead, match="already in the pipeline"):
        save(Lead(company="Northwind Ltd", email="ops@northwind.co.uk", notes="dupe"), cfg.inbox)


def test_a_website_only_lead_is_fine(cfg):
    """Plenty of real prospects have no published email — the agent researches one."""
    lead = Lead(
        company="Harbour Dental",
        website="https://harbourdental.example-clinic.nz",
        notes="Front desk answers booking calls manually during surgery hours.",
    )
    assert save(lead, cfg.inbox).exists()


def test_importing_a_batch_survives_duplicates(cfg, tmp_path):
    """One bad row must not cost you the rest of the batch."""
    import json

    from earner.leads import import_file

    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps({"leads": [
        {"company": "Real One", "website": "https://realone.dev", "notes": "manual quote desk"},
        {"company": "Real Two", "website": "https://realtwo.dev", "notes": "slow inbox"},
        {"company": "Fake", "email": "test@example.com", "notes": "invented"},
    ]}))

    added, skipped = import_file(batch, cfg.inbox)
    assert [lead.company for lead in added] == ["Real One", "Real Two"]
    assert len(skipped) == 1 and "placeholder domain" in skipped[0]

    added, skipped = import_file(batch, cfg.inbox)
    assert added == [] and len(skipped) == 3, "re-importing must add nothing"

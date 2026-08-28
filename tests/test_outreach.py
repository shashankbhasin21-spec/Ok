"""Tests for the outreach pipeline.

The tests that matter are the ones proving it cannot misbehave while nobody is
watching: that it will not send twice, will not send to someone who opted out,
will not invoice a stranger, and will not open a socket without both gates.
"""

from __future__ import annotations


import pytest

from earner.outreach import (BOUNCED, CLOSED, DAILY_CAP, DRAFTED, MAX_STEPS,
                             OPTED_OUT, QUOTED, REPLIED, RESEARCHED, SENT,
                             SEND_CONFIRMATION, SEND_PHRASE, WON, Campaign,
                             OutreachError, Pipeline, Prospect, build_message,
                             check_replies, collect, looks_like_optout, quote,
                             run_sends, send, sending_enabled)


@pytest.fixture
def pipeline(tmp_path):
    p = Pipeline(tmp_path / "outreach.db")
    yield p
    p.close()


@pytest.fixture
def campaign():
    return Campaign(name="test", service="an audit of your last 200 loads",
                    from_name="Tester", price_low=1500, price_high=2500,
                    hook="rate con vs invoice", signature="Tester")


def prospect(email="a@example.test", company="Acme Freight"):
    return Prospect(company=company, email=email,
                    evidence="Your careers page lists two billing clerks whose job "
                             "is matching rate confirmations to carrier invoices.")


# ── intake ──────────────────────────────────────────────────────────────────

def test_a_prospect_without_evidence_is_refused():
    with pytest.raises(OutreachError, match="too thin"):
        Prospect(company="Acme", email="a@b.test", evidence="freight").validate()


def test_a_prospect_without_a_real_address_is_refused():
    with pytest.raises(OutreachError, match="receive mail"):
        Prospect(company="Acme", email="notanemail",
                 evidence="A long enough piece of evidence to pass the length check.").validate()


def test_the_same_address_cannot_enter_the_pipeline_twice(pipeline):
    assert pipeline.add(prospect(), "test")
    assert pipeline.add(prospect(), "test") == ""
    assert len(pipeline.at_stage(RESEARCHED)) == 1


def test_case_and_spacing_do_not_create_a_duplicate(pipeline):
    pipeline.add(prospect(email="  A@Example.Test "), "test")
    assert pipeline.add(prospect(email="a@example.test"), "test") == ""


def test_a_suppressed_address_is_never_added_back(pipeline):
    pipeline.suppress("a@example.test")
    assert pipeline.add(prospect(), "test") == ""


# ── the state machine ───────────────────────────────────────────────────────

def test_a_prospect_cannot_skip_from_researched_to_won(pipeline):
    ref = pipeline.add(prospect(), "test")
    with pytest.raises(OutreachError, match="cannot go"):
        pipeline.advance(ref, WON)


def test_an_opted_out_prospect_is_terminal(pipeline):
    ref = pipeline.add(prospect(), "test")
    pipeline.advance(ref, OPTED_OUT)
    for stage in (DRAFTED, SENT, REPLIED, WON):
        with pytest.raises(OutreachError):
            pipeline.advance(ref, stage)


def test_every_terminal_stage_has_no_way_out():
    from earner.outreach import TRANSITIONS
    for stage in (WON, BOUNCED, OPTED_OUT, CLOSED):
        assert TRANSITIONS[stage] == set()


def test_advancing_an_unknown_prospect_is_an_error(pipeline):
    with pytest.raises(OutreachError, match="unknown"):
        pipeline.advance("p-nope", DRAFTED)


# ── drafting ────────────────────────────────────────────────────────────────

def test_the_subject_is_short_and_carries_no_price(pipeline, campaign):
    subject = campaign.subject(prospect())
    assert len(subject.split()) <= 8
    assert "$" not in subject


def test_the_body_contains_the_evidence_verbatim(pipeline, campaign):
    body = campaign.body(prospect())
    assert "matching rate confirmations to carrier invoices" in body


def test_every_message_carries_an_opt_out(pipeline, campaign):
    assert "STOP" in campaign.body(prospect())
    for step in (1, 2):
        assert "STOP" in campaign.follow_up(prospect(), step)[1]


def test_drafting_moves_researched_prospects_and_nothing_else(pipeline, campaign):
    pipeline.add(prospect(), "test")
    pipeline.add(prospect(email="b@example.test", company="Bee Lines"), "test")
    assert pipeline.draft_all(campaign) == 2
    assert pipeline.draft_all(campaign) == 0
    assert len(pipeline.at_stage(DRAFTED)) == 2


# ── sending: the gates ──────────────────────────────────────────────────────

def test_a_dry_run_never_opens_a_socket(campaign):
    message = build_message(to="a@example.test", subject="s", body="b",
                            from_address="me@gmail.test")
    # No SMTP server exists in the test environment; if this tried to connect
    # it would raise rather than return a Message-ID.
    assert send(message, user="me@gmail.test", app_password="x", live=False).startswith("<")


def test_live_sending_refuses_without_the_environment_confirmation(campaign):
    message = build_message(to="a@example.test", subject="s", body="b",
                            from_address="me@gmail.test")
    with pytest.raises(OutreachError, match="refusing to send"):
        send(message, user="me@gmail.test", app_password="x", live=True, env={})


def test_the_confirmation_phrase_must_match_exactly():
    assert sending_enabled({SEND_CONFIRMATION: SEND_PHRASE})
    assert not sending_enabled({SEND_CONFIRMATION: "yes"})
    assert not sending_enabled({SEND_CONFIRMATION: SEND_PHRASE.lower()})
    assert not sending_enabled({})


def test_a_message_is_threaded_when_it_is_a_follow_up():
    first = build_message(to="a@example.test", subject="s", body="b",
                          from_address="me@gmail.test")
    second = build_message(to="a@example.test", subject="Re: s", body="b",
                           from_address="me@gmail.test", in_reply_to=first["Message-ID"])
    assert second["In-Reply-To"] == first["Message-ID"]
    assert second["References"] == first["Message-ID"]


# ── sending: the volume controls ────────────────────────────────────────────

def _seed(pipeline, campaign, n):
    for i in range(n):
        pipeline.add(prospect(email=f"a{i}@example.test", company=f"Co {i}"), campaign.name)
    pipeline.draft_all(campaign)


def test_the_daily_cap_is_obeyed(pipeline, campaign):
    _seed(pipeline, campaign, 30)
    sent = run_sends(pipeline, campaign, user="me@gmail.test", app_password="x",
                     cap=5, live=False, sleeper=lambda _: None, log=lambda *_: None)
    assert sent == 5
    assert pipeline.sent_today() == 5


def test_the_cap_counts_what_was_already_sent_today(pipeline, campaign):
    _seed(pipeline, campaign, 30)
    common = dict(user="me@gmail.test", app_password="x", live=False,
                  sleeper=lambda _: None, log=lambda *_: None)
    run_sends(pipeline, campaign, cap=5, **common)
    assert run_sends(pipeline, campaign, cap=5, **common) == 0
    assert pipeline.remaining_today(5) == 0


def test_the_default_cap_is_conservative():
    assert DAILY_CAP <= 25, "a personal Gmail does not survive a bigger burst"


def test_a_prospect_suppressed_after_drafting_is_not_sent_to(pipeline, campaign):
    pipeline.add(prospect(), campaign.name)
    pipeline.draft_all(campaign)
    pipeline.suppress("a@example.test")
    sent = run_sends(pipeline, campaign, user="me@gmail.test", app_password="x",
                     live=False, sleeper=lambda _: None, log=lambda *_: None)
    assert sent == 0
    assert len(pipeline.at_stage(OPTED_OUT)) == 1


def test_sends_are_spaced_apart(pipeline, campaign):
    _seed(pipeline, campaign, 3)
    pauses = []
    run_sends(pipeline, campaign, user="me@gmail.test", app_password="x", live=False,
              sleeper=pauses.append, log=lambda *_: None)
    assert len(pauses) == 2                    # not after the last one
    assert all(p > 0 for p in pauses)
    assert len(set(pauses)) > 1, "identical gaps are a bot signature"


# ── follow-ups ──────────────────────────────────────────────────────────────

def test_follow_ups_are_not_due_immediately(pipeline, campaign):
    _seed(pipeline, campaign, 1)
    run_sends(pipeline, campaign, user="m@e.test", app_password="x", live=False,
              sleeper=lambda _: None, log=lambda *_: None)
    assert pipeline.due_follow_ups() == []


def test_a_follow_up_fires_once_its_date_arrives(pipeline, campaign):
    _seed(pipeline, campaign, 1)
    run_sends(pipeline, campaign, user="m@e.test", app_password="x", live=False,
              sleeper=lambda _: None, log=lambda *_: None)
    pipeline.db.execute("update prospects set next_action_at = 1")
    pipeline.db.commit()
    assert len(pipeline.due_follow_ups()) == 1
    run_sends(pipeline, campaign, user="m@e.test", app_password="x", live=False,
              sleeper=lambda _: None, log=lambda *_: None)
    row = pipeline.at_stage(SENT)[0]
    assert row["step"] == 1


def test_follow_ups_stop_after_the_limit(pipeline, campaign):
    _seed(pipeline, campaign, 1)
    for _ in range(MAX_STEPS + 3):
        pipeline.db.execute("update prospects set next_action_at = 1")
        pipeline.db.commit()
        run_sends(pipeline, campaign, user="m@e.test", app_password="x", live=False,
                  sleeper=lambda _: None, log=lambda *_: None)
    row = pipeline.at_stage(SENT)
    assert not row or row[0]["step"] <= MAX_STEPS


def test_an_exhausted_prospect_is_closed_and_never_mailed_again(pipeline, campaign):
    _seed(pipeline, campaign, 1)
    run_sends(pipeline, campaign, user="m@e.test", app_password="x", live=False,
              sleeper=lambda _: None, log=lambda *_: None)
    pipeline.db.execute("update prospects set step=?, next_action_at=1", (MAX_STEPS,))
    pipeline.db.commit()
    assert pipeline.close_exhausted() == 1
    assert len(pipeline.at_stage(CLOSED)) == 1
    assert pipeline.due_follow_ups() == []


# ── opt-outs ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "STOP", "please unsubscribe me", "Remove me from your list",
    "opt out", "do not contact me again", "take me off this",
])
def test_opt_out_language_is_recognised(text):
    assert looks_like_optout(text)


def test_an_ordinary_reply_is_not_an_opt_out():
    assert not looks_like_optout("Sounds interesting, can you send more detail?")


def test_an_opt_out_is_permanent(pipeline):
    ref = pipeline.add(prospect(), "test")
    pipeline.suppress("a@example.test")
    assert pipeline.suppressed("a@example.test")
    assert pipeline.add(prospect(), "test") == ""
    del ref


def test_check_replies_does_nothing_when_not_live(pipeline):
    assert check_replies(pipeline, user="m@e.test", app_password="x", live=False) == 0


# ── the money ───────────────────────────────────────────────────────────────

@pytest.fixture
def money(tmp_path):
    from earner.ledger import Ledger
    from earner.payments import SandboxProvider
    ledger = Ledger(tmp_path / "ledger.db")
    yield ledger, SandboxProvider(tmp_path / "sandbox.json")
    ledger.close()


def test_someone_who_never_replied_cannot_be_invoiced(pipeline, campaign, money):
    ledger, provider = money
    ref = pipeline.add(prospect(), campaign.name)
    pipeline.draft_all(campaign)
    with pytest.raises(OutreachError, match="only someone who answered"):
        quote(pipeline, ref, ledger=ledger, provider=provider, campaign=campaign,
              amount_cents=150000)


def test_a_reply_becomes_a_job_and_a_payable_invoice(pipeline, campaign, money):
    ledger, provider = money
    ref = pipeline.add(prospect(), campaign.name)
    pipeline.draft_all(campaign)
    pipeline.advance(ref, SENT)
    pipeline.advance(ref, REPLIED)
    out = quote(pipeline, ref, ledger=ledger, provider=provider, campaign=campaign,
                amount_cents=180000)
    assert out["url"]
    assert ledger.get_job(out["job_id"]).quote_cents == 180000
    assert pipeline.at_stage(QUOTED)


def test_revenue_appears_only_after_the_provider_confirms(pipeline, campaign, money):
    ledger, provider = money
    ref = pipeline.add(prospect(), campaign.name)
    pipeline.draft_all(campaign)
    pipeline.advance(ref, SENT)
    pipeline.advance(ref, REPLIED)
    out = quote(pipeline, ref, ledger=ledger, provider=provider, campaign=campaign,
                amount_cents=180000)

    assert collect(pipeline, ledger=ledger, provider=provider, log=lambda *_: None) == 0
    assert ledger.revenue_cents() == 0

    provider.mark_paid(out["provider_ref"])
    assert collect(pipeline, ledger=ledger, provider=provider, log=lambda *_: None) == 1
    assert ledger.revenue_cents() == 180000
    assert pipeline.at_stage(WON)


def test_collecting_twice_does_not_double_count_revenue(pipeline, campaign, money):
    ledger, provider = money
    ref = pipeline.add(prospect(), campaign.name)
    pipeline.draft_all(campaign)
    pipeline.advance(ref, SENT)
    pipeline.advance(ref, REPLIED)
    out = quote(pipeline, ref, ledger=ledger, provider=provider, campaign=campaign,
                amount_cents=180000)
    provider.mark_paid(out["provider_ref"])
    collect(pipeline, ledger=ledger, provider=provider, log=lambda *_: None)
    collect(pipeline, ledger=ledger, provider=provider, log=lambda *_: None)
    assert ledger.revenue_cents() == 180000


# ── resumability ────────────────────────────────────────────────────────────

def test_state_survives_the_process_dying(tmp_path, campaign):
    first = Pipeline(tmp_path / "o.db")
    first.add(prospect(), campaign.name)
    first.draft_all(campaign)
    run_sends(first, campaign, user="m@e.test", app_password="x", live=False,
              sleeper=lambda _: None, log=lambda *_: None)
    first.close()

    second = Pipeline(tmp_path / "o.db")
    assert second.summary()[SENT] == 1
    assert second.sent_today() == 1
    second.close()

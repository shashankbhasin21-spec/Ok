"""Tests for the speed engine.

The thesis under test: freshness dominates. These assert that a good match
posted hours ago loses to a decent one posted minutes ago, because if that
ordering ever inverts the module has stopped doing the one thing it is for.
"""

from __future__ import annotations

import time

import pytest

from earner.watch import (FRESHNESS_HALFLIFE_MINUTES, STALE_AFTER_MINUTES,
                          Opening, Profile, Rank, Seen, fit, poll, render,
                          score, shortlist)

NOW = 1_800_000_000.0


def job(**kw) -> Opening:
    base = dict(title="Python automation for invoice reconciliation",
                url="https://example.test/1", source="test",
                description="Build a Python script and API integration",
                budget_low=200.0, budget_high=800.0, bids=3,
                posted_at=NOW - 10 * 60, client_country="United States",
                payment_verified=True)
    base.update(kw)
    return Opening(**base)


# ── freshness ───────────────────────────────────────────────────────────────

def test_a_brand_new_posting_is_maximally_fresh():
    assert job(posted_at=NOW).freshness(NOW) == pytest.approx(1.0)


def test_freshness_decays_to_about_a_third_after_the_half_life():
    fresh = job(posted_at=NOW - FRESHNESS_HALFLIFE_MINUTES * 60).freshness(NOW)
    assert 0.33 < fresh < 0.40


def test_a_stale_posting_is_worth_exactly_nothing():
    assert job(posted_at=NOW - (STALE_AFTER_MINUTES + 1) * 60).freshness(NOW) == 0.0


def test_an_unknown_post_time_is_treated_as_old_not_new():
    # Otherwise a source that omits timestamps would jump every queue.
    assert job(posted_at=0).age_minutes(NOW) == 120.0
    assert job(posted_at=0).freshness(NOW) < job(posted_at=NOW - 3600).freshness(NOW) * 1.01


def test_freshness_never_goes_negative_on_a_future_timestamp():
    assert job(posted_at=NOW + 9999).freshness(NOW) == pytest.approx(1.0)


# ── the ordering that is the whole point ────────────────────────────────────

def test_a_fresh_decent_job_outranks_a_stale_perfect_one():
    fresh = job(title="Python script", description="python automation",
                budget_high=300.0, posted_at=NOW - 5 * 60, url="a")
    stale = job(title="Python LLM agent pipeline ETL API automation data",
                description="python llm agent api etl pipeline automation data claude",
                budget_high=5000.0, posted_at=NOW - 5 * 3600, url="b")
    assert score(fresh, now=NOW).score > score(stale, now=NOW).score


def test_the_shortlist_is_ordered_best_first():
    jobs = [job(url=f"u{i}", posted_at=NOW - i * 600) for i in range(1, 5)]
    ranked = shortlist(jobs, now=NOW)
    assert [r.score for r in ranked] == sorted((r.score for r in ranked), reverse=True)


def test_a_shortlist_of_nothing_is_empty_not_an_error():
    assert shortlist([], now=NOW) == []


# ── blockers ────────────────────────────────────────────────────────────────

def test_work_outside_the_profile_is_blocked_with_a_reason():
    rank = score(job(title="Wordpress theme and logo design",
                     description="customise our wordpress theme"), now=NOW)
    assert not rank.worth_bidding
    assert "outside what you build" in rank.blockers[0]


def test_a_crowded_posting_is_blocked():
    rank = score(job(bids=90), now=NOW)
    assert not rank.worth_bidding
    assert "bids already" in " ".join(rank.blockers)


def test_a_budget_under_the_floor_is_blocked():
    rank = score(job(budget_high=40.0), Profile(floor_usd=100.0), now=NOW)
    assert not rank.worth_bidding
    assert "below your" in " ".join(rank.blockers)


def test_a_posting_with_no_skill_overlap_is_blocked():
    rank = score(job(title="Walk my dog", description="dog walking in Ohio"), now=NOW)
    assert not rank.worth_bidding


def test_a_blocked_posting_scores_zero_however_fresh_it_is():
    assert score(job(title="logo design", posted_at=NOW), now=NOW).score == 0.0


# ── fit ─────────────────────────────────────────────────────────────────────

def test_fit_saturates_so_a_keyword_stuffed_posting_cannot_win_on_length():
    stuffed = job(description=" ".join(Profile().skills))
    modest = job(description="python automation api integration data")
    assert fit(stuffed, Profile())[0] == fit(modest, Profile())[0] == 1.0


def test_fit_reports_what_matched_so_the_alert_can_explain_itself():
    quality, matched, avoided = fit(job(), Profile())
    assert quality > 0 and "python" in matched and avoided == []


# ── the cold-start setting ──────────────────────────────────────────────────

def test_the_default_floor_is_low_enough_for_a_first_review():
    # A client risking $100-$500 will take a chance on a zero-review profile.
    # The first jobs are for the reviews, not the money.
    assert Profile().floor_usd <= 100.0


def test_the_bid_ceiling_is_tighter_than_the_old_bidding_agent():
    from earner.agents.bidding import BiddingAgent
    assert Profile().ceiling_bids < BiddingAgent.MAX_BID_COUNT


def test_a_verified_paying_client_outranks_an_unverified_one():
    verified = score(job(payment_verified=True, url="a"), now=NOW).score
    plain = score(job(payment_verified=False, client_country="", url="b"), now=NOW).score
    assert verified > plain


# ── memory ──────────────────────────────────────────────────────────────────

def test_a_posting_is_only_surfaced_once(tmp_path):
    memory = Seen(tmp_path / "w.db")
    opening = job()
    assert memory.is_new(opening)
    memory.remember(Rank(0.9, opening))
    assert not memory.is_new(opening)
    memory.close()


def test_polling_twice_does_not_re_alert_the_same_job(tmp_path):
    memory = Seen(tmp_path / "w.db")
    source = lambda: [job(posted_at=time.time() - 300)]      # noqa: E731
    assert len(poll([source], memory=memory)) == 1
    assert poll([source], memory=memory) == []
    memory.close()


def test_a_broken_source_does_not_end_the_sweep(tmp_path, capsys):
    def broken():
        raise RuntimeError("board is down")

    def working():
        return [job(posted_at=time.time() - 120)]

    ranked = poll([broken, working])
    assert len(ranked) == 1
    assert "board is down" in capsys.readouterr().out


def test_stats_count_what_was_surfaced(tmp_path):
    memory = Seen(tmp_path / "w.db")
    memory.remember(Rank(0.9, job()))
    memory.mark_acted(job().ref)
    assert memory.stats() == {"seen": 1, "acted": 1}
    memory.close()


# ── the alert ───────────────────────────────────────────────────────────────

def test_the_alert_leads_with_age_because_that_is_the_decision():
    text = render(shortlist([job(posted_at=NOW - 6 * 60)], now=NOW))
    assert "min old" in text


def test_an_empty_alert_says_so_rather_than_printing_nothing():
    assert render([]) == "nothing fresh"


def test_the_alert_carries_the_url_so_it_can_be_acted_on_from_a_phone():
    text = render(shortlist([job(url="https://example.test/xyz")], now=NOW))
    assert "https://example.test/xyz" in text

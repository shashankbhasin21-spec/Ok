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


# ── word matching: the bug that put JavaScript jobs at the top ──────────────

def test_a_skill_matches_as_a_word_not_a_substring():
    from earner.watch import mentions
    # "script" inside "JavaScript" and "bot" inside "robot" both scored as core
    # matches, which put full-stack JS work above Python automation work.
    assert not mentions("script", "build a javascript app")
    assert mentions("script", "write a python script")
    assert not mentions("bot", "a robot arm for the factory")
    assert mentions("bot", "build a telegram bot")


def test_dotted_and_multiword_skills_still_match():
    from earner.watch import mentions
    assert mentions("make.com", "automate this using make.com please")
    assert mentions("web scraping", "we need web scraping for 500 sites")


def test_a_javascript_posting_is_not_a_core_skill_match():
    posting = job(title="Full-Stack Developer for Node.js App",
                  description="JavaScript, TypeScript, React frontend work",
                  skills=[])
    assert fit(posting, Profile())[0] == 0.0


# ── weighted skills ─────────────────────────────────────────────────────────

def test_a_strong_skill_outweighs_several_weak_ones():
    strong = job(title="Web scraping script", description="python web scraping", skills=[])
    weak = job(title="API data integration", description="api data integration json rest", skills=[])
    assert fit(strong, Profile())[0] > fit(weak, Profile())[0]


def test_weak_skills_alone_are_not_a_match():
    # "api" and "data" appear in nearly every development posting.
    generic = job(title="Developer needed", description="api data integration", skills=[])
    quality, _, _ = fit(generic, Profile())
    assert quality == 0.0


def test_a_posting_with_no_core_match_says_which_kind_of_miss_it_was():
    rank = score(job(title="Developer", description="api data", skills=[]), now=NOW)
    assert "no core-skill match" in " ".join(rank.blockers)


# ── the avoid rule, refined ─────────────────────────────────────────────────

def test_an_avoided_term_in_the_title_blocks_the_posting():
    rank = score(job(title="Next.js social app development",
                     description="python automation backend"), now=NOW)
    assert not rank.worth_bidding


def test_a_tangential_skill_tag_does_not_veto_a_real_match():
    # A scraping job that also tags "graphic design" among eight skills is
    # still a scraping job; vetoing it threw away genuine matches.
    posting = job(title="Web scraping and data extraction",
                  description="python web scraping into postgres",
                  skills=["python", "web scraping", "graphic design"])
    assert score(posting, now=NOW).worth_bidding


# ── the RSS source ──────────────────────────────────────────────────────────

FEED = """<rss><channel>
<item>
  <title><![CDATA[Web Scraping Script &amp; Data Export]]></title>
  <link>https://www.freelancer.com/projects/x/scrape.html</link>
  <description><![CDATA[Scrape 500 pages... (Budget: $30 - $250 USD, Jobs: Python, Web Scraping)]]></description>
  <pubDate>Fri, 28 Aug 2026 10:13:04 -0400</pubDate>
  <category><![CDATA[Python]]></category>
  <category><![CDATA[Web Scraping]]></category>
</item>
<item>
  <title><![CDATA[Hourly Python Work]]></title>
  <link>https://www.freelancer.com/projects/x/hourly.html</link>
  <description><![CDATA[Ongoing... (Budget: $25 USD / hr, Jobs: Python)]]></description>
  <pubDate>Fri, 28 Aug 2026 10:11:00 -0400</pubDate>
</item>
<item>
  <title><![CDATA[Rupee Job]]></title>
  <link>https://www.freelancer.com/projects/x/inr.html</link>
  <description><![CDATA[Edit... (Budget: 400 - 750 INR, Jobs: Video Editing)]]></description>
  <pubDate>Fri, 28 Aug 2026 10:10:00 -0400</pubDate>
</item>
</channel></rss>"""


def test_the_feed_parser_reads_title_budget_currency_and_time():
    from earner.watch import parse_freelancer_rss
    items = parse_freelancer_rss(FEED)
    assert len(items) == 3
    first = items[0]
    assert first.title == "Web Scraping Script & Data Export"      # entity unescaped
    assert (first.budget_low, first.budget_high) == (30.0, 250.0)
    assert first.currency == "usd"
    assert first.posted_at > 0
    assert "python" in first.skills


def test_an_hourly_posting_gets_a_bounded_stand_in_budget():
    from earner.watch import parse_freelancer_rss
    hourly = parse_freelancer_rss(FEED)[1]
    # Unbounded hourly work must not outrank a fixed scope just by being open.
    assert hourly.budget_high == 25.0 * 20


def test_a_non_dollar_currency_is_read_from_the_feed():
    from earner.watch import parse_freelancer_rss
    assert parse_freelancer_rss(FEED)[2].currency == "inr"


def test_a_malformed_feed_yields_nothing_rather_than_raising():
    from earner.watch import parse_freelancer_rss
    assert parse_freelancer_rss("<rss><channel></channel></rss>") == []
    assert parse_freelancer_rss("not xml at all") == []


def test_keyword_feeds_are_built_one_per_keyword():
    from earner.watch import FREELANCER_KEYWORDS, freelancer_sources
    assert len(freelancer_sources()) == len(FREELANCER_KEYWORDS)


# ── deduplication within one sweep ──────────────────────────────────────────

def test_the_same_posting_from_two_feeds_is_listed_once():
    # Overlapping keyword feeds return the same job repeatedly; without this
    # the alert showed it two and three times and pushed real matches off.
    duplicate = lambda: [job(url="https://same.test/1", posted_at=time.time() - 300)]  # noqa: E731
    assert len(poll([duplicate, duplicate, duplicate])) == 1

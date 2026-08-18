"""What the system has learned, and whether it acts on it.

The test that matters most is the last one: the rules must refuse the project's
own best strategy. Knowledge that exempts its owner's favourite idea is not
knowledge, it is a rationalisation.
"""

from __future__ import annotations

import pytest

from quant_os.agents.learned import (
    LESSONS, LESSONS_BY_NAME, Proposal, curriculum, observations_needed, review,
)


def test_every_lesson_carries_its_evidence():
    """A rule without a measurement behind it is an opinion."""
    assert len(LESSONS) >= 10
    for lesson in LESSONS:
        assert lesson.evidence and any(ch.isdigit() for ch in lesson.evidence), \
            f"{lesson.name} has no numbers in its evidence"
        assert lesson.rule, f"{lesson.name} states a finding but no rule"


def test_lesson_names_are_unique():
    assert len(LESSONS_BY_NAME) == len(LESSONS)


# ── refusals ────────────────────────────────────────────────────────────────

def test_intraday_is_refused_outright():
    """Measured to lose at zero cost, so no execution improvement rescues it."""
    r = review(Proposal("scalper", holding_sessions=0))
    assert not r.permitted
    assert any("intraday_has_no_edge" in x for x in r.refusals)


def test_shorting_a_bearish_candle_is_refused():
    r = review(Proposal("short stars", holding_sessions=10,
                        direction="short", signal_type="candle"))
    assert not r.permitted
    assert any("bearish_candles_are_inverted" in x for x in r.refusals)


def test_going_long_on_a_candle_is_not_refused_for_direction():
    """The finding is about shorts specifically, and the rule must not overreach."""
    r = review(Proposal("long hammer", holding_sessions=10,
                        direction="long", signal_type="candle"))
    assert not any("bearish_candles_are_inverted" in x for x in r.refusals)


def test_an_edge_not_measured_against_the_base_rate_is_refused():
    r = review(Proposal("raw returns", holding_sessions=10,
                        measured_edge=0.02, base_rate_adjusted=False))
    assert any("patterns_measure_drift" in x for x in r.refusals)


def test_a_non_positive_edge_is_refused_before_anything_else_is_discussed():
    r = review(Proposal("losing", holding_sessions=10,
                        measured_edge=-0.001, base_rate_adjusted=True))
    assert any("non-positive" in x for x in r.refusals)


def test_a_wide_search_on_a_short_sample_is_refused():
    """The lesson from 5,000 meaningless rules producing Sharpe 2.20."""
    r = review(Proposal("overfit", holding_sessions=10, candidates_tested=5_000,
                        observations=100, measured_edge=0.05,
                        base_rate_adjusted=True))
    assert any("data_depth_bounds_what_is_knowable" in x for x in r.refusals)


def test_the_same_edge_is_permitted_with_enough_observations():
    r = review(Proposal("patient", holding_sessions=10, candidates_tested=5_000,
                        observations=50_000, measured_edge=0.05,
                        base_rate_adjusted=True, regime_filtered=True))
    assert r.permitted, r.report()


# ── warnings, which inform rather than block ────────────────────────────────

def test_widening_a_ranked_signal_warns_but_does_not_block():
    r = review(Proposal("wide momentum", holding_sessions=21, signal_type="momentum",
                        universe_size=30, regime_filtered=True))
    assert r.permitted
    assert any("concentration_beats_breadth" in w for w in r.warnings)


def test_an_unfiltered_strategy_is_warned_about_regime():
    r = review(Proposal("no filter", holding_sessions=21, regime_filtered=False))
    assert any("regime_filters_earn_their_place" in w for w in r.warnings)


def test_a_short_hold_warns_about_the_cost_floor():
    r = review(Proposal("three day", holding_sessions=3, regime_filtered=True))
    assert r.permitted
    assert any("holding_period_sets_the_cost_floor" in w for w in r.warnings)


# ── the sample-size arithmetic ──────────────────────────────────────────────

def test_more_candidates_demand_more_evidence():
    assert observations_needed(0.1, 10) < observations_needed(0.1, 1_000)


def test_a_bigger_effect_needs_less_evidence():
    assert observations_needed(0.3, 100) < observations_needed(0.05, 100)


def test_a_non_positive_effect_can_never_be_established():
    assert observations_needed(0.0, 100) > 10 ** 8


# ── the test that matters ───────────────────────────────────────────────────

def test_the_rules_refuse_this_projects_own_best_strategy():
    """Cross-sectional momentum, +15.4%/yr, the best thing measured here — and
    still refused, because 105 observations after 64 candidates is not enough.

    A system whose rules exempt its author's favourite idea has not learned
    anything; it has written down a preference.
    """
    best = Proposal("momentum 3 names, filtered", holding_sessions=63,
                    signal_type="momentum", universe_size=3,
                    candidates_tested=64, observations=105,
                    measured_edge=0.033, base_rate_adjusted=True,
                    regime_filtered=True)
    r = review(best)
    assert not r.permitted
    assert any("data_depth" in x for x in r.refusals)


def test_the_curriculum_is_readable():
    text = curriculum()
    assert "evidence:" in text and "rule:" in text
    assert len(text.splitlines()) >= len(LESSONS) * 3

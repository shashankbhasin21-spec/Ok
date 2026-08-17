"""The research lab: scoring, splitting, and the defences against fooling yourself.

The tests that matter here are the ones about selection bias. A research loop
that keeps whatever scored best is a machine for manufacturing false positives,
and these pin down the three things that stop it.
"""

from __future__ import annotations

import pytest

from earner.trading.research import (
    RANGES, WEIGHTS, Metrics, Split, Trial, Verdict,
    days_needed, expected_max_sharpe, hypotheses, measure, sharpe_standard_error,
    split_days,
)


def days(n: int, start: int = 1) -> list[str]:
    return [f"2026-06-{i:02d}" for i in range(start, start + n)]


# ── scoring ─────────────────────────────────────────────────────────────────

def test_the_weights_are_a_full_allocation():
    assert round(sum(WEIGHTS.values()), 6) == 1.0
    assert set(WEIGHTS) == set(RANGES)


def test_a_perfect_strategy_scores_one_and_a_terrible_one_zero():
    perfect = Metrics(sharpe=3.0, max_drawdown=0.0, annual_return=1.0,
                      win_rate=0.8, profit_factor=3.0)
    awful = Metrics(sharpe=-1.0, max_drawdown=0.5, annual_return=-0.2,
                    win_rate=0.2, profit_factor=0.5)
    assert perfect.score == 1.0
    assert awful.score == 0.0


def test_metrics_past_the_range_do_not_score_above_one():
    """A Sharpe of 40 on ten days is noise, not a fortieth-percentile genius."""
    absurd = Metrics(sharpe=40.0, max_drawdown=0.0, annual_return=50.0,
                     win_rate=1.0, profit_factor=99.0)
    assert absurd.score == 1.0


def test_drawdown_is_scored_inverted():
    """Less drawdown must score higher — the range is deliberately reversed."""
    shallow = Metrics(max_drawdown=0.0).score
    deep = Metrics(max_drawdown=0.5).score
    assert shallow > deep


def test_measure_turns_daily_pnl_into_metrics():
    m = measure([1_000.0, -500.0, 1_500.0, -200.0], capital=100_000, trades=8)
    assert m.days == 4 and m.trades == 8
    assert m.net == 1_800.0
    assert m.win_rate == 0.5
    assert m.profit_factor == pytest.approx(2_500 / 700, rel=1e-3)


def test_a_flat_series_has_no_sharpe_rather_than_an_infinite_one():
    """Zero volatility is an absence of information, not perfect quality."""
    assert measure([0.0, 0.0, 0.0], capital=100_000, trades=0).sharpe == 0.0


def test_drawdown_is_peak_to_trough():
    m = measure([1_000.0, -400.0, -400.0, -400.0], capital=100_000, trades=4)
    # Stored to four decimals, so the tolerance matches that, not float exactness.
    assert m.max_drawdown == pytest.approx(1_200 / 101_000, abs=1e-4)


def test_no_days_is_an_empty_result_not_a_crash():
    assert measure([], capital=100_000, trades=0).score >= 0.0


# ── the split ───────────────────────────────────────────────────────────────

def test_the_split_is_chronological_and_three_way():
    """Shuffled splits let a strategy see the future. Order is the whole point."""
    s = split_days(days(30))
    assert s.train and s.validation and s.test
    assert max(s.train) < min(s.validation) < min(s.test)


def test_an_embargo_separates_the_parts():
    """Indicators look backwards, so adjacent bars leak across the boundary."""
    s = split_days(days(30), embargo=2)
    train_last = int(s.train[-1][-2:])
    val_first = int(s.validation[0][-2:])
    assert val_first - train_last > 1, "there must be a gap, not a seam"


def test_too_little_history_is_refused_rather_than_split_anyway():
    with pytest.raises(ValueError, match="too few"):
        split_days(days(6))


# ── the multiple-testing threshold ──────────────────────────────────────────

def test_more_trials_means_a_higher_bar():
    """Searching more variants raises the best result you get from luck alone."""
    ten = expected_max_sharpe(10, 250)
    hundred = expected_max_sharpe(100, 250)
    thousand = expected_max_sharpe(1_000, 250)
    assert 0 < ten < hundred < thousand


def test_a_single_trial_has_no_selection_bias():
    assert expected_max_sharpe(1, 250) == 0.0


def test_more_data_lowers_the_bar():
    """The threshold is noise; more observations means less of it."""
    assert expected_max_sharpe(50, 1_000) < expected_max_sharpe(50, 100)


def test_short_samples_have_useless_sharpe_estimates():
    """The finding that decides whether any of this is science: ten days of
    data gives a Sharpe estimate wrong by ±5."""
    assert sharpe_standard_error(10) > 4.0
    assert sharpe_standard_error(250) == pytest.approx(1.0, abs=0.01)
    assert sharpe_standard_error(2_500) < 0.35


def test_identifying_a_real_edge_needs_years_not_weeks():
    """Sixty days of intraday history cannot validate anything, however good
    the engineering around it is."""
    needed = days_needed(target_sharpe=1.0, trials=20)
    assert needed > 800, "roughly four years of daily observations"
    assert days_needed(2.0, 20) < needed, "a bigger edge is easier to detect"
    assert days_needed(1.0, 200) > needed, "more searching needs more evidence"


# ── the verdict ─────────────────────────────────────────────────────────────

def trial(name, val_sharpe, score_hint=0.0):
    return Trial(hypothesis=name,
                 train=Metrics(sharpe=val_sharpe),
                 validation=Metrics(sharpe=val_sharpe, annual_return=score_hint))


def test_nothing_surviving_is_a_reported_result_not_an_error():
    verdict = Verdict(trials=[trial("a", 0.4), trial("b", 0.2)], threshold=1.5,
                      split=Split(days(20), days(5, 21), days(5, 26)))
    assert verdict.survived is False
    assert "nothing survived" in verdict.report()


def test_the_report_warns_when_the_sample_cannot_decide():
    verdict = Verdict(trials=[trial("a", 3.0)], threshold=0.1,
                      split=Split(days(20), days(5, 21), days(5, 26)))
    report = verdict.report()
    assert "CANNOT REACH A CONCLUSION" in report
    assert "years" in report


def test_a_candidate_that_fails_the_held_out_test_is_not_a_survivor():
    """The whole reason for a third split: clearing a search you steered is
    not the same as working on data the search never saw."""
    winner = trial("looked-good", 2.5)
    verdict = Verdict(trials=[winner], threshold=1.0,
                      split=Split(days(20), days(5, 21), days(5, 26)),
                      survivor=winner, test=Metrics(sharpe=-0.8))
    assert verdict.survived is False
    assert "failed the held-out test" in verdict.report()


def test_a_genuine_survivor_earns_paper_trading_not_capital():
    winner = trial("held-up", 2.5)
    verdict = Verdict(trials=[winner], threshold=1.0,
                      split=Split(days(20), days(5, 21), days(5, 26)),
                      survivor=winner, test=Metrics(sharpe=1.4))
    assert verdict.survived is True
    assert "PAPER TRADING, not capital" in verdict.report()


# ── the hypothesis space ────────────────────────────────────────────────────

def test_the_space_covers_the_parameters_that_were_guessed():
    names = [h.name for h in hypotheses()]
    assert len(names) == len(set(names)), "no duplicate hypotheses"
    assert any("orb" in n for n in names)
    assert any("reversion" in n for n in names)
    assert any("+" in n for n in names), "combinations are hypotheses too"


def test_each_hypothesis_builds_a_fresh_strategy_set():
    """Shared mutable strategies between trials would leak state across them."""
    h = hypotheses()[0]
    assert h.strategies() is not h.strategies()

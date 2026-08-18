"""Event store and validation statistics.

The event-store tests are about the journal being trustworthy: gapless,
append-only, and provably so. The metrics tests are about the statistics being
right, checked against cases where the answer is known independently.
"""

from __future__ import annotations

import random

import pytest

from quant_os.ledger.event_store import (
    FILL, ORDER, SIGNAL, EventStore, EventStoreError, correlation_id,
)
from quant_os.validation.metrics import (
    deflated_sharpe, expected_max_sharpe, inverse_normal_cdf, kurtosis,
    minimum_track_record, normal_cdf, probabilistic_sharpe, skewness,
)


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "events.db")
    yield s
    s.close()


# ── the journal ─────────────────────────────────────────────────────────────

def test_events_get_a_gapless_monotonic_sequence(store):
    for i in range(5):
        store.append(SIGNAL, "engine", f"e{i}")
    assert [e.seq for e in store.replay()] == [1, 2, 3, 4, 5]
    assert store.verify() == []


def test_one_decision_can_be_traced_end_to_end(store):
    """A signal, its order and its fill, pulled out of a day of noise."""
    cid = correlation_id()
    store.append(SIGNAL, "engine", "orb_long", correlation=cid, symbol="RELIANCE")
    store.append(SIGNAL, "engine", "unrelated")          # noise
    store.append(ORDER, "execution", "submitted", correlation=cid)
    store.append(FILL, "broker", "filled", correlation=cid, price=1402.75)

    trace = store.trace(cid)
    assert [e.event for e in trace] == ["orb_long", "submitted", "filled"]
    assert trace[-1].payload["price"] == 1402.75


def test_an_unknown_event_kind_is_refused(store):
    """The set is closed so a typo cannot create a category nothing queries."""
    with pytest.raises(EventStoreError, match="unknown event kind"):
        store.append("signl", "engine", "typo")


def test_replay_is_ordered_and_filterable(store):
    store.append(SIGNAL, "engine", "a")
    store.append(FILL, "broker", "b")
    store.append(SIGNAL, "engine", "c")

    assert [e.event for e in store.replay(kind=SIGNAL)] == ["a", "c"]
    assert [e.event for e in store.replay(since=1)] == ["b", "c"]


def test_verify_detects_a_gap(store):
    """A journal is only useful if you can show it is complete."""
    for i in range(4):
        store.append(SIGNAL, "engine", f"e{i}")
    store.conn.execute("DELETE FROM events WHERE seq=2")
    store.conn.commit()

    problems = store.verify()
    assert problems and "sequence gap" in problems[0]


def test_payloads_survive_a_round_trip(store):
    store.append(ORDER, "execution", "sized", quantity=30, price=1402.75,
                 reasons=["ev_ok", "risk_ok"])
    payload = store.replay()[0].payload
    assert payload["quantity"] == 30
    assert payload["reasons"] == ["ev_ok", "risk_ok"]


# ── normal distribution helpers ─────────────────────────────────────────────

def test_normal_cdf_matches_known_values():
    assert normal_cdf(0.0) == pytest.approx(0.5)
    assert normal_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
    assert normal_cdf(-1.96) == pytest.approx(0.025, abs=1e-3)


def test_inverse_normal_cdf_round_trips():
    for p in (0.01, 0.25, 0.5, 0.75, 0.99):
        assert normal_cdf(inverse_normal_cdf(p)) == pytest.approx(p, abs=1e-6)


# ── moments ─────────────────────────────────────────────────────────────────

def test_skew_and_kurtosis_of_a_symmetric_sample():
    random.seed(3)
    sample = [random.gauss(0, 1) for _ in range(5_000)]
    assert abs(skewness(sample)) < 0.15
    assert kurtosis(sample) == pytest.approx(3.0, abs=0.3), "non-excess convention"


def test_a_left_tail_shows_as_negative_skew():
    """Wins small and often, loses catastrophically — the shape that flatters
    a Sharpe ratio and ruins an account."""
    sample = [0.01] * 99 + [-1.0]
    assert skewness(sample) < -5


# ── PSR and DSR ─────────────────────────────────────────────────────────────

def test_psr_rises_with_sample_size():
    """The same Sharpe is more believable from more observations."""
    random.seed(5)
    short = [random.gauss(0.001, 0.01) for _ in range(30)]
    long_ = short * 20
    assert probabilistic_sharpe(long_) > probabilistic_sharpe(short)


def test_psr_punishes_negative_skew():
    """Mirrored samples: identical mean, identical spread, opposite skew.

    Mirroring rather than hand-building two lists, because two samples that
    merely look different usually differ in mean too, and then the test is
    measuring the mean.
    """
    right = [0.02] * 5 + [0.001] * 95            # rare large win
    mean = sum(right) / len(right)
    left = [2 * mean - x for x in right]          # rare large loss

    assert skewness(right) > 0 > skewness(left)
    assert sum(left) / len(left) == pytest.approx(mean)
    assert probabilistic_sharpe(left) < probabilistic_sharpe(right)


def test_a_flat_series_is_not_certainty():
    assert probabilistic_sharpe([0.0] * 50) == pytest.approx(0.0, abs=0.6)


def test_the_luck_benchmark_grows_with_the_number_of_trials():
    ten = expected_max_sharpe(10, 250)
    thousand = expected_max_sharpe(1_000, 250)
    assert 0 < ten < thousand
    assert expected_max_sharpe(1, 250) == 0.0, "one trial has no selection bias"


def test_more_data_lowers_the_luck_benchmark():
    assert expected_max_sharpe(50, 2_000) < expected_max_sharpe(50, 100)


def test_deflated_sharpe_collapses_as_the_search_widens():
    """The whole point: a result that looks good after 1 trial is worthless
    after 500, and the number must say so."""
    random.seed(7)
    returns = [random.gauss(0.0009, 0.01) for _ in range(250)]
    one, fifty, five_hundred = (deflated_sharpe(returns, n) for n in (1, 50, 500))
    assert one > fifty > five_hundred
    # A result that reads as near-certain after one look becomes a coin flip
    # once you admit how many looks it took to find.
    assert one > 0.95
    assert five_hundred < 0.5


def test_minimum_track_record_is_infinite_for_a_losing_strategy():
    """No amount of data makes a negative edge significant."""
    assert minimum_track_record([-0.001] * 100) == float("inf")


def test_minimum_track_record_shrinks_as_the_edge_grows():
    random.seed(9)
    weak = [random.gauss(0.0003, 0.01) for _ in range(500)]
    strong = [random.gauss(0.0030, 0.01) for _ in range(500)]
    assert minimum_track_record(strong) < minimum_track_record(weak)

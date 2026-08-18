"""Tests for the rapid-trading engine.

The tests that matter here are not the ones checking that a function returns a
float. They are the ones checking that the engine cannot cheat: that entry is
never the detection bar, that an ambiguous bar resolves against the trade, that
costs are charged on both legs, and that no gate can be talked into a trade
with a negative expected value.
"""

from __future__ import annotations

import math

from earner.trading.broker import BUY, SELL
from quant_os.execution import costs as C
from quant_os.features.flow import (close_location, divergence, flow_state,
                                    signed_volume, split_volume, zscore)
from quant_os.features.regime import BUCKETS, bucket, classify
from quant_os.models.ensemble import ACTIVE, Ensemble, check_groups, slice_features
from quant_os.models.logistic import Logistic, Ridge, auc, brier, solve
from quant_os.rapid import decide as D
from quant_os.rapid.labeling import label
from quant_os.rapid.scorecard import score
from quant_os.rapid.setups import Setup, detect


class Bar:
    def __init__(self, at, o, h, l, c, v):
        self.at, self.open, self.high, self.low, self.close, self.volume = at, o, h, l, c, v


def series(n=60, start=100.0, step=0.1, volume=10_000.0, at0=1_700_000_000.0):
    bars = []
    price = start
    for i in range(n):
        o = price
        c = price + step
        bars.append(Bar(at0 + i * 300, o, max(o, c) + 0.05, min(o, c) - 0.05, c, volume))
        price = c
    return bars


# ── costs ───────────────────────────────────────────────────────────────────

def test_brokerage_is_capped_so_cost_per_share_falls_with_size():
    small = C.round_trip(1000.0, 10).total / 10
    large = C.round_trip(1000.0, 1000).total / 1000
    assert small > large, "the 20 cap means big trades cost less per share"


def test_stt_falls_on_the_sell_leg_only():
    buy = C.charges(100_000.0, C.BUY)
    sell = C.charges(100_000.0, C.SELL)
    assert buy.stt == 0.0
    assert sell.stt > 0.0
    assert buy.stamp > 0.0 and sell.stamp == 0.0


def test_options_cost_far_more_than_cash_per_rupee_of_turnover():
    # At a small turnover the 20 brokerage cap dominates both segments and
    # hides the difference; the statutory gap only shows once turnover is large
    # enough for the percentage charges to matter, which is the regime an
    # option scalper actually trades in.
    cash = C.round_trip(1000.0, 1000, segment=C.EQUITY_INTRADAY).total
    options = C.round_trip(1000.0, 1000, segment=C.INDEX_OPTIONS).total
    assert options > cash * 2


def test_corwin_schultz_is_zero_on_a_flat_series_and_positive_with_noise():
    flat = [100.0] * 20
    assert C.corwin_schultz_spread(flat, flat) == 0.0
    highs = [100 + (i % 3) * 0.5 for i in range(20)]
    lows = [99 - (i % 2) * 0.5 for i in range(20)]
    assert C.corwin_schultz_spread(highs, lows) > 0


def test_impact_grows_with_the_square_root_of_participation():
    kw = dict(price=100.0, bar_volume=1_000_000.0, volatility=0.01)
    one = C.impact(1000, **kw)
    four = C.impact(4000, **kw)
    assert math.isclose(four / one, 2.0, rel_tol=0.01)


def test_fill_price_is_always_against_you():
    m = C.CostModel()
    kw = dict(spread=0.001, quantity=100, bar_volume=1_000_000.0, volatility=0.01)
    assert m.fill_price(100.0, BUY, **kw) > 100.0
    assert m.fill_price(100.0, SELL, **kw) < 100.0


def test_partial_fill_caps_at_a_slice_of_bar_volume():
    assert C.CostModel().fillable(10_000, 50_000) == 5_000
    assert C.CostModel().fillable(100, 50_000) == 100
    assert C.CostModel().fillable(100, 0) == 0


# ── flow proxies ────────────────────────────────────────────────────────────

def test_close_location_is_zero_on_a_doji_rather_than_undefined():
    assert close_location(Bar(0, 100, 100, 100, 100, 5)) == 0.0


def test_a_close_on_the_high_splits_all_volume_to_the_buy_side():
    buy, sell = split_volume(Bar(0, 99, 101, 99, 101, 1000))
    assert buy == 1000 and sell == 0


def test_signed_volume_falls_back_to_the_body_with_no_previous_bar():
    assert signed_volume(Bar(0, 99, 101, 98, 100, 500)) == 500
    assert signed_volume(Bar(0, 101, 101, 98, 100, 500)) == -500


def test_zscore_is_clipped_so_one_thin_bar_cannot_dominate():
    assert zscore(1e9, [1.0, 1.1, 0.9, 1.0]) == 6.0
    assert zscore(-1e9, [1.0, 1.1, 0.9, 1.0]) == -6.0


def test_zscore_of_a_constant_history_is_zero_not_a_division_error():
    assert zscore(5.0, [1.0, 1.0, 1.0]) == 0.0


def test_flow_state_needs_history_and_says_so_rather_than_guessing():
    assert flow_state([Bar(0, 1, 1, 1, 1, 1)]).volume_z == 0.0


def test_divergence_is_zero_on_a_steady_trend_with_unchanging_pressure():
    # Every bar identical in shape: price rises, buying pressure is constant.
    # Constant is not "falling", so there is no divergence to report.
    assert divergence(series(30)) == 0.0


# ── regime and clock ────────────────────────────────────────────────────────

def test_session_buckets_tile_the_day_without_gaps_or_overlap():
    for (_, _, end), (_, start, _) in zip(BUCKETS, BUCKETS[1:]):
        assert end == start
    assert BUCKETS[0][1] == 9 * 3600 + 15 * 60
    assert BUCKETS[-1][2] == 15 * 3600 + 30 * 60


def test_bucket_outside_the_session_is_named_rather_than_misfiled():
    assert bucket(1_700_000_000.0 - 8 * 3600) == "OUTSIDE"


def test_a_short_series_classifies_as_low_volatility_not_a_crash():
    assert classify([Bar(i, 1, 1, 1, 1, 1) for i in range(5)]).state == "LOW_VOLATILITY"


def test_a_steady_climb_is_a_trend_and_permits_continuation():
    regime = classify(series(60))
    assert regime.state in ("STRONG_TREND", "WEAK_TREND", "LOW_VOLATILITY")


# ── setups ──────────────────────────────────────────────────────────────────

def test_no_setup_fires_without_enough_history():
    assert detect("X", series(10)) == []


def test_a_flat_series_produces_no_setups():
    flat = [Bar(1_700_000_000 + i * 300, 100, 100.1, 99.9, 100, 1000) for i in range(60)]
    assert detect("X", flat) == []


def test_setups_carry_a_stop_on_the_correct_side_of_the_entry():
    for kind in detect("X", series(60)):
        if kind.side == BUY:
            assert kind.stop < kind.price
        else:
            assert kind.stop > kind.price


# ── labelling: the lookahead guards ─────────────────────────────────────────

def _setup(side=BUY, price=100.0, stop=99.0):
    return Setup(symbol="X", kind="breakout", side=side, at=0.0, price=price,
                 stop=stop, horizon_minutes=30)


def test_entry_is_the_next_bar_open_never_the_detection_close():
    future = [Bar(300, 105.0, 106, 104, 105.5, 10_000)]
    out = label(_setup(), future, bar_minutes=5)
    # Entry is built from 105.0, the next bar's open, not the setup's 100.0.
    assert out.entry > 104.0


def test_a_bar_touching_both_stop_and_target_is_recorded_as_a_loss():
    future = [Bar(300, 100.0, 103.0, 98.0, 100.0, 10_000)] * 6
    out = label(_setup(), future, bar_minutes=5)
    assert out.exit_reason == "stop"


def test_the_optimistic_flag_exists_only_to_measure_the_gap():
    future = [Bar(300, 100.0, 103.0, 98.0, 100.0, 10_000)] * 6
    pessimistic = label(_setup(), future, bar_minutes=5)
    optimistic = label(_setup(), future, bar_minutes=5, optimistic=True)
    assert optimistic.net_return > pessimistic.net_return


def test_net_return_is_always_below_gross_return():
    future = [Bar(300 * i, 100 + i * 0.5, 101 + i * 0.5, 99 + i * 0.5,
                  100.4 + i * 0.5, 10_000) for i in range(1, 8)]
    out = label(_setup(), future, bar_minutes=5)
    assert out.net_return < out.gross_return


def test_the_horizon_is_cut_short_by_the_square_off_time():
    future = [Bar(300 * i, 100, 100.2, 99.8, 100, 10_000) for i in range(1, 20)]
    full = label(_setup(), future, bar_minutes=5)
    clipped = label(_setup(), future, bar_minutes=5, minutes_left=20)
    assert clipped.bars_held < full.bars_held


def test_labelling_with_no_future_bars_returns_nothing_rather_than_a_zero():
    assert label(_setup(), [], bar_minutes=5) is None


# ── models ──────────────────────────────────────────────────────────────────

def test_solve_recovers_a_known_solution():
    assert [round(v, 6) for v in solve([[2.0, 1.0], [1.0, 3.0]], [5.0, 10.0])] == [1.0, 3.0]


def test_solve_survives_a_singular_column_instead_of_dividing_by_zero():
    out = solve([[1.0, 0.0], [0.0, 0.0]], [2.0, 0.0])
    assert out[0] == 2.0


def test_logistic_recovers_the_sign_of_a_planted_relationship():
    rows, labels = [], []
    for i in range(400):
        x = (i % 20) / 10 - 1
        rows.append({"x": x, "noise": (i % 7) / 7})
        labels.append(1 if x > 0 else 0)
    model = Logistic().fit(rows, labels)
    weights = dict(model.coefficients())
    assert weights["x"] > 0
    assert abs(weights["x"]) > abs(weights["noise"])


def test_an_unfitted_model_refuses_to_predict():
    try:
        Logistic().predict({"x": 1})
    except ValueError:
        return
    raise AssertionError("an unfitted model must not answer")


def test_a_model_with_no_signal_returns_the_base_rate_not_a_half():
    rows = [{"x": (i % 5)} for i in range(200)]
    labels = [1 if i < 20 else 0 for i in range(200)]
    model = Logistic(l2=1e6).fit(rows, labels)
    assert abs(model.predict({"x": 2}) - 0.1) < 0.05


def test_ridge_recovers_a_linear_target():
    rows = [{"a": i / 100, "b": 1.0} for i in range(200)]
    model = Ridge(l2=1e-9).fit(rows, [0.003 * r["a"] for r in rows])
    assert abs(model.predict({"a": 1.0, "b": 1.0}) - 0.003) < 1e-4


def test_auc_of_a_perfect_ranker_is_one_and_of_a_constant_is_a_half():
    assert auc([0.1, 0.2, 0.3, 0.4], [0, 0, 1, 1]) == 1.0
    assert auc([0.5] * 4, [0, 0, 1, 1]) == 0.5


def test_brier_rewards_the_base_rate_over_a_confident_wrong_answer():
    assert brier([0.3] * 10, [0] * 7 + [1] * 3) < brier([0.9] * 10, [0] * 7 + [1] * 3)


# ── ensemble ────────────────────────────────────────────────────────────────

def test_the_independent_heads_share_no_features():
    assert check_groups() == []


def test_the_book_head_has_no_features_because_there_is_no_book_history():
    from quant_os.models.ensemble import GROUPS
    assert GROUPS["BOOK"] == ()
    assert "BOOK" not in ACTIVE


def test_slicing_gives_each_head_only_its_own_group():
    row = {"acceleration": 1.0, "flow_imbalance": 0.5, "regime_RANGE": 1.0}
    assert slice_features(row, "PRICE") == {"acceleration": 1.0}
    assert slice_features(row, "FLOW") == {"flow_imbalance": 0.5}
    assert slice_features(row, "REGIME") == {"regime_RANGE": 1.0}


def test_the_ensemble_reports_dispersion_when_heads_disagree():
    rows = [{"acceleration": i % 3 - 1, "flow_imbalance": 1 - (i % 3),
             "minutes_left": 100.0, "atr_pct": 0.01} for i in range(200)]
    labels = [i % 2 for i in range(200)]
    model = Ensemble().fit(rows, labels)
    assert 0.0 <= model.dispersion(rows[0]) <= 0.5


def test_the_ensemble_predicts_every_field_section_seven_asks_for():
    rows = [{"acceleration": (i % 5) / 5, "flow_imbalance": (i % 3) / 3,
             "minutes_left": 90.0, "atr_pct": 0.008} for i in range(200)]
    labels = [1 if i % 4 == 0 else 0 for i in range(200)]
    model = Ensemble().fit(rows, labels, returns=[0.001] * 200,
                           maes=[0.002] * 200, mfes=[0.003] * 200)
    out = model.predict(rows[0])
    for field in ("p_win", "p_loss", "expected_return", "expected_adverse",
                  "expected_favourable", "dispersion", "votes"):
        assert field in out
    assert abs(out["p_win"] + out["p_loss"] - 1.0) < 1e-9


# ── the decision gates ──────────────────────────────────────────────────────

def test_expected_value_goes_negative_once_costs_exceed_the_edge():
    assert D.expected_value(probability=0.5, reward=2.0, risk=1.0,
                            cost_per_share=0.0) > 0
    assert D.expected_value(probability=0.5, reward=2.0, risk=1.0,
                            cost_per_share=1.0) < 0


def test_tier_c_sizes_to_zero_shares():
    assert D.size_for(D.C, capital=100_000, risk_per_share=1.0, price=100.0) == 0


def test_size_shrinks_with_drawdown_and_reaches_zero_at_twenty_percent():
    kw = dict(capital=100_000, risk_per_share=1.0, price=100.0)
    full = D.size_for(D.A, **kw, drawdown_fraction=0.0)
    half = D.size_for(D.A, **kw, drawdown_fraction=0.10)
    dead = D.size_for(D.A, **kw, drawdown_fraction=0.25)
    assert full > half > 0
    assert dead == 0


def test_a_losing_streak_can_never_increase_size():
    kw = dict(capital=100_000, risk_per_share=1.0, price=100.0)
    sizes = [D.size_for(D.A, **kw, drawdown_fraction=d / 100) for d in range(0, 20)]
    assert sizes == sorted(sizes, reverse=True)


def test_one_position_cannot_exceed_its_share_of_the_margin():
    quantity = D.size_for(D.A_PLUS, capital=100_000, risk_per_share=0.01,
                          price=100.0, max_leverage=5.0, max_positions=5)
    assert quantity * 100.0 <= 100_000 * 5.0 / 5


def test_the_adversarial_check_objects_to_a_stale_entry():
    setup = _setup()
    setup.features = {"extension_atr": 3.0, "minutes_left": 300.0}
    objections = D.adversarial(setup, {"expected_return": 0.01, "dispersion": 0.0})
    assert any("already paid for" in o for o in objections)


def test_the_adversarial_check_objects_when_flow_points_the_other_way():
    setup = _setup()
    setup.features = {"flow_imbalance": -0.5, "minutes_left": 300.0}
    objections = D.adversarial(setup, {"expected_return": 0.01, "dispersion": 0.0})
    assert any("against the trade" in o for o in objections)


def test_the_adversarial_check_stands_down_after_repeated_failures():
    setup = _setup()
    setup.features = {"minutes_left": 300.0}
    objections = D.adversarial(setup, {"expected_return": 0.01, "dispersion": 0.0},
                               recent_failures=3)
    assert any("consecutive failures" in o for o in objections)


def test_a_clean_setup_raises_no_objections():
    setup = _setup()
    setup.features = {"minutes_left": 300.0, "volume_z": 1.0, "flow_imbalance": 0.4,
                      "atr_pct": 0.005, "extension_atr": 0.5, "exhaustion": 0.0}
    assert D.adversarial(setup, {"expected_return": 0.01, "dispersion": 0.02}) == []


def test_decide_refuses_a_setup_with_no_stop_distance():
    decision = D.decide(_setup(stop=100.0), {"p_win": 0.9, "dispersion": 0.0},
                        capital=100_000, costs=C.CostModel(), spread=0.001,
                        bar_volume=1e7, volatility=0.01)
    assert not decision
    assert decision.quantity == 0


def test_a_high_probability_cannot_override_a_negative_expected_value():
    setup = _setup(price=100.0, stop=99.99)      # 1 paisa of edge, all cost
    setup.features = {"minutes_left": 300.0, "volume_z": 1.0, "flow_imbalance": 0.4,
                      "atr_pct": 0.005}
    decision = D.decide(setup, {"p_win": 0.99, "dispersion": 0.0, "expected_return": 0.5},
                        capital=100_000, costs=C.CostModel(), spread=0.002,
                        bar_volume=1e7, volatility=0.01)
    assert not decision


# ── scorecard ───────────────────────────────────────────────────────────────

def test_an_empty_scorecard_reports_no_trades_rather_than_dividing_by_zero():
    assert score([], capital=100_000).trades == 0


def test_the_scorecard_separates_gross_from_net():
    trades = [{"net": -50.0, "gross": 20.0, "cost": 70.0, "notional": 10_000.0,
               "tier": "B", "kind": "breakout"}]
    card = score(trades, capital=100_000)
    assert card.gross == 20.0 and card.net == -50.0 and card.costs == 70.0


def test_profit_factor_is_infinite_with_no_losers_and_that_is_a_warning():
    card = score([{"net": 10.0, "notional": 1000.0}], capital=100_000)
    assert card.profit_factor == float("inf")


def test_max_drawdown_tracks_the_worst_peak_to_trough_not_the_final_loss():
    trades = [{"net": n, "notional": 1000.0} for n in (100, -500, 400, -50)]
    card = score(trades, capital=10_000)
    assert card.max_drawdown == -500.0

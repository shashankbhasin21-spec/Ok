"""Indicators, signals and regime classification.

The important assertions here are the *refusals*. A strategy that fires on
every bar is a random number generator with a thesis attached, so most of
these tests check that a plausible-looking setup is correctly declined.
"""

from __future__ import annotations

from earner.trading.broker import BUY, SELL
from earner.trading.strategy import (
    Candle, MeanReversion, OpeningRangeBreakout, Signal, VWAPMomentum,
    atr, classify_regime, ema, opening_range, rsi, vwap,
)


def series(closes, volumes=None, spread=0.05) -> list[Candle]:
    """Candles from a close series, with a plausible high/low around each move."""
    out, prev = [], closes[0]
    for i, close in enumerate(closes):
        volume = volumes[i] if volumes else 10_000.0
        out.append(Candle(
            at=float(i * 60), open=prev,
            high=max(prev, close) + spread, low=min(prev, close) - spread,
            close=close, volume=volume,
        ))
        prev = close
    return out


def zigzag(start: float, up: float, down: float, count: int) -> list[float]:
    """An uptrend that actually pulls back, so RSI lands in a tradeable band."""
    closes, price = [start], start
    for i in range(count - 1):
        price += up if i % 2 == 0 else -down
        closes.append(round(price, 4))
    return closes


# ── signal arithmetic ───────────────────────────────────────────────────────

def test_reward_to_risk_comes_from_the_stop_not_from_hope():
    signal = Signal(symbol="X", side=BUY, strategy="t", confidence=0.6,
                    entry=100.0, stop=98.0, target=106.0, thesis="", invalidation="")
    assert signal.risk_per_share == 2.0
    assert signal.reward_per_share == 6.0
    assert signal.reward_to_risk == 3.0


def test_expected_value_is_negative_when_the_edge_does_not_pay_for_the_risk():
    """A 3:1 loser at 50% is a losing trade however good the story is."""
    signal = Signal(symbol="X", side=BUY, strategy="t", confidence=0.5,
                    entry=100.0, stop=97.0, target=101.0, thesis="", invalidation="")
    assert signal.expected_value(0.5) < 0
    assert signal.expected_value(0.9, cost_per_share=0.03) > 0


def test_costs_can_turn_a_positive_edge_negative():
    """The reason paper engines lie: a thin edge is entirely eaten by costs."""
    signal = Signal(symbol="X", side=BUY, strategy="t", confidence=0.55,
                    entry=100.0, stop=99.9, target=100.12, thesis="", invalidation="")
    assert signal.expected_value(0.55) > 0
    assert signal.expected_value(0.55, cost_per_share=0.05) < 0


def test_a_zero_risk_signal_does_not_divide_by_zero():
    signal = Signal(symbol="X", side=BUY, strategy="t", confidence=0.6,
                    entry=100.0, stop=100.0, target=110.0, thesis="", invalidation="")
    assert signal.reward_to_risk == 0.0


# ── indicators ──────────────────────────────────────────────────────────────

def test_ema_needs_a_full_period_before_it_says_anything():
    assert ema([1.0, 2.0, 3.0], 5) is None
    assert ema([10.0] * 20, 9) == 10.0


def test_vwap_is_volume_weighted_not_price_averaged():
    """Two bars, one with ten times the volume — VWAP is pulled to the heavy one.

    VWAP uses each bar's typical price ((H+L+C)/3), so the heavy bar contributes
    166.67 rather than its 200 close; the point is that it dominates the 100.
    """
    candles = [
        Candle(at=0.0, open=100.0, high=100.0, low=100.0, close=100.0, volume=1_000.0),
        Candle(at=60.0, open=200.0, high=200.0, low=200.0, close=200.0, volume=10_000.0),
    ]
    assert round(vwap(candles), 2) == 190.91      # not the 150 an unweighted mean gives


def test_vwap_of_a_zero_volume_window_is_unknown_not_zero():
    assert vwap(series([100.0, 101.0], volumes=[0.0, 0.0])) is None


def test_atr_measures_range_and_needs_history():
    assert atr(series([100.0] * 5)) is None
    steady = atr(series([100.0 + i for i in range(30)]))
    assert 1.0 < steady < 1.3      # 1.0 of move plus the 0.05 spreads


def test_rsi_pins_at_the_extremes():
    assert rsi([100.0 + i for i in range(20)]) == 100.0      # no down closes
    assert rsi([100.0 - i for i in range(20)]) == 0.0        # no up closes
    assert 45 < rsi(zigzag(100.0, 1.0, 1.0, 20)) < 55        # balanced


def test_opening_range_is_the_first_n_minutes_only():
    closes = [100.0] * 15 + [130.0] * 10          # the spike is outside the range
    high, low = opening_range(series(closes), minutes=15)
    assert high < 101 and low > 99
    assert opening_range(series([100.0] * 5), minutes=15) is None


# ── opening range breakout ──────────────────────────────────────────────────

def _breakout(volume_multiple: float) -> list[Candle]:
    closes = [100.0] * 15 + [100.0 + i * 0.5 for i in range(1, 16)]
    volumes = [10_000.0] * 15 + [10_000.0 * volume_multiple] * 15
    return series(closes, volumes=volumes)


def test_a_break_on_expanding_volume_is_a_signal():
    signal = OpeningRangeBreakout().evaluate("RELIANCE", _breakout(3.0))
    assert signal is not None
    assert signal.side == BUY
    assert signal.stop < signal.entry < signal.target
    assert signal.invalidation and signal.thesis, "a signal must state what kills it"


def test_a_break_without_participation_is_refused():
    """Same price action, ordinary volume. This is the noise filter."""
    assert OpeningRangeBreakout().evaluate("RELIANCE", _breakout(1.0)) is None


def test_a_downside_break_sells():
    closes = [100.0] * 15 + [100.0 - i * 0.5 for i in range(1, 16)]
    volumes = [10_000.0] * 15 + [30_000.0] * 15
    signal = OpeningRangeBreakout().evaluate("TCS", series(closes, volumes=volumes))
    assert signal is not None and signal.side == SELL
    assert signal.stop > signal.entry > signal.target


def test_no_breakout_signal_before_there_is_a_range():
    assert OpeningRangeBreakout().evaluate("X", series([100.0] * 20)) is None


# ── VWAP momentum ───────────────────────────────────────────────────────────

def test_vwap_momentum_fires_on_a_trend_that_still_has_room():
    signal = VWAPMomentum().evaluate("HDFCBANK", series(zigzag(100.0, 1.0, 0.6, 60)))
    assert signal is not None
    assert signal.side == BUY
    assert signal.stop < signal.entry < signal.target
    assert 50 < signal.metrics["rsi"] < 72


def test_vwap_momentum_refuses_an_overextended_trend():
    """A vertical ramp is RSI 100 — the trade is already gone."""
    assert VWAPMomentum().evaluate("X", series([100.0 + i for i in range(60)])) is None


def test_vwap_momentum_needs_enough_history():
    assert VWAPMomentum().evaluate("X", series(zigzag(100.0, 1.0, 0.6, 30))) is None


# ── mean reversion ──────────────────────────────────────────────────────────

def _stretched_down() -> list[Candle]:
    closes = [100.0, 100.4] * 20 + [100.0 - i * 0.3 for i in range(1, 15)]
    return series(closes)


def test_mean_reversion_buys_a_stretch_below_vwap():
    signal = MeanReversion().evaluate("SUNPHARMA", _stretched_down())
    assert signal is not None
    assert signal.side == BUY
    assert signal.target == signal.metrics["vwap"], "the target is the mean it reverts to"
    assert signal.metrics["stretch_atr"] <= -2.0


def test_mean_reversion_will_not_touch_a_trend():
    """Only in a range: fading a real trend is how small losses become large."""
    assert MeanReversion().regimes == ("RANGE", "NEUTRAL")
    assert "STRONG_BEAR" not in MeanReversion().regimes


def test_price_sitting_on_vwap_is_not_a_stretch():
    assert MeanReversion().evaluate("X", series([100.0, 102.0] * 30)) is None


def test_a_stretch_that_is_already_bouncing_is_refused():
    """Stretch alone is not the setup — RSI has to agree it is still oversold.

    Here price is 2.2 ATR under VWAP but RSI has recovered to 39: the move it
    would have faded has already been faded by somebody else.
    """
    closes = ([100.0, 100.4] * 20
              + [100.0 - i * 0.3 for i in range(1, 11)]
              + [97.0 + i * 0.35 for i in range(1, 6)])
    assert MeanReversion().evaluate("X", series(closes)) is None


# ── regime ──────────────────────────────────────────────────────────────────

def test_regime_reads_trend_and_volatility():
    assert classify_regime(series([100.0 + i * 0.5 for i in range(60)])) == "STRONG_BULL"
    assert classify_regime(series([100.0 - i * 0.5 for i in range(60)])) == "STRONG_BEAR"
    assert classify_regime(series([100.0, 100.05] * 30)) == "RANGE"


def test_a_violent_tape_is_flagged_rather_than_traded():
    """Two percent ATR is not an opportunity, it is a spread you cannot cross."""
    assert classify_regime(series(zigzag(100.0, 5.0, 5.0, 60))) == "EXTREME_VOLATILITY"


def test_too_little_data_is_neutral_not_a_guess():
    assert classify_regime(series([100.0] * 10)) == "NEUTRAL"

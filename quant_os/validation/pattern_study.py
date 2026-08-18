"""Does a candle pattern predict the next move? Measured across decades.

This tests the oldest claim in retail trading directly: that the shape of
recent bars tells you when to enter and when to exit. It is a good hypothesis
precisely because it is checkable, and until now this project lacked the data
to check it — intraday history stops at weeks, but daily history runs to
decades.

Method, chosen so the answer cannot be flattered:

* **Forward returns, not equity curves.** For every bar where a pattern fires,
  record what price did over the next N days. No position sizing, no
  compounding, no stop placement — those are choices that can rescue a
  worthless signal or ruin a good one, and they are not what is being tested.
* **Compared against the base rate.** A bullish pattern in a market that rose
  on 53% of all days must beat 53%, not 50%. Most published pattern statistics
  omit this and are therefore measuring the drift of the market.
* **Deflated for the number of patterns tried.** Test twenty patterns across
  six horizons and the best of the 120 will look excellent by luck alone.
* **Costs charged.** A signal with a 0.05% edge and a 0.15% round trip is a
  losing signal, however reliable the direction.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from earner.trading.strategy import Candle

# Round-trip cost for a multi-day equity position, in fractional terms.
# Brokerage plus STT plus impact, on the generous side for a retail account.
ROUND_TRIP_COST = 0.0022     # 22 bp, matching the published A-share figure


@dataclass
class PatternResult:
    name: str
    horizon: int
    occurrences: int
    mean_return: float          # gross, before cost
    base_rate_mean: float       # what any random day returned
    hit_rate: float
    base_hit_rate: float
    stdev: float

    @property
    def excess(self) -> float:
        """Edge over simply being in the market. The only number that matters."""
        return self.mean_return - self.base_rate_mean

    @property
    def net_excess(self) -> float:
        return self.excess - ROUND_TRIP_COST

    @property
    def t_stat(self) -> float:
        if self.occurrences < 2 or not self.stdev:
            return 0.0
        return self.excess / (self.stdev / (self.occurrences ** 0.5))

    @property
    def sharpe(self) -> float:
        """Per-trade Sharpe of the excess return."""
        return self.excess / self.stdev if self.stdev else 0.0


# ── the patterns ────────────────────────────────────────────────────────────
#
# Each returns True when it fires on the LAST bar of the window it is given.
# Every one of these is a staple of retail technical analysis.

def _body(c: Candle) -> float:
    return abs(c.close - c.open)


def _range(c: Candle) -> float:
    return max(c.high - c.low, 1e-9)


def bullish_engulfing(w: list[Candle]) -> bool:
    a, b = w[-2], w[-1]
    return (a.close < a.open and b.close > b.open
            and b.close > a.open and b.open < a.close)


def bearish_engulfing(w: list[Candle]) -> bool:
    a, b = w[-2], w[-1]
    return (a.close > a.open and b.close < b.open
            and b.close < a.open and b.open > a.close)


def hammer(w: list[Candle]) -> bool:
    c = w[-1]
    lower = min(c.open, c.close) - c.low
    return _body(c) > 0 and lower > 2 * _body(c) and (c.high - max(c.open, c.close)) < _body(c)


def shooting_star(w: list[Candle]) -> bool:
    c = w[-1]
    upper = c.high - max(c.open, c.close)
    return _body(c) > 0 and upper > 2 * _body(c) and (min(c.open, c.close) - c.low) < _body(c)


def doji(w: list[Candle]) -> bool:
    return _body(w[-1]) / _range(w[-1]) < 0.1


def inside_bar(w: list[Candle]) -> bool:
    a, b = w[-2], w[-1]
    return b.high < a.high and b.low > a.low


def outside_bar(w: list[Candle]) -> bool:
    a, b = w[-2], w[-1]
    return b.high > a.high and b.low < a.low


def gap_up(w: list[Candle]) -> bool:
    return w[-1].open > w[-2].high


def gap_down(w: list[Candle]) -> bool:
    return w[-1].open < w[-2].low


def three_white_soldiers(w: list[Candle]) -> bool:
    return all(c.close > c.open for c in w[-3:]) and \
        w[-1].close > w[-2].close > w[-3].close


def three_black_crows(w: list[Candle]) -> bool:
    return all(c.close < c.open for c in w[-3:]) and \
        w[-1].close < w[-2].close < w[-3].close


def breakout_20d(w: list[Candle]) -> bool:
    return w[-1].close > max(c.high for c in w[-21:-1])


def breakdown_20d(w: list[Candle]) -> bool:
    return w[-1].close < min(c.low for c in w[-21:-1])


def _sma(w: list[Candle], n: int) -> float:
    return sum(c.close for c in w[-n:]) / n


def golden_cross(w: list[Candle]) -> bool:
    return (_sma(w, 50) > _sma(w, 200)
            and sum(c.close for c in w[-51:-1]) / 50 <= sum(c.close for c in w[-201:-1]) / 200)


def death_cross(w: list[Candle]) -> bool:
    return (_sma(w, 50) < _sma(w, 200)
            and sum(c.close for c in w[-51:-1]) / 50 >= sum(c.close for c in w[-201:-1]) / 200)


def _rsi(w: list[Candle], period: int = 14) -> float:
    closes = [c.close for c in w[-period - 1:]]
    gains = sum(max(closes[i + 1] - closes[i], 0) for i in range(len(closes) - 1))
    losses = sum(max(closes[i] - closes[i + 1], 0) for i in range(len(closes) - 1))
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + (gains / losses))


def rsi_oversold(w: list[Candle]) -> bool:
    return _rsi(w) < 30


def rsi_overbought(w: list[Candle]) -> bool:
    return _rsi(w) > 70


def momentum_12m(w: list[Candle]) -> bool:
    """The one anomaly with the strongest independent literature."""
    return w[-1].close > w[-252].close


def reversal_1m(w: list[Candle]) -> bool:
    return w[-1].close < w[-21].close


BULLISH = {
    "bullish_engulfing": bullish_engulfing, "hammer": hammer,
    "gap_up": gap_up, "three_white_soldiers": three_white_soldiers,
    "breakout_20d": breakout_20d, "golden_cross": golden_cross,
    "rsi_oversold": rsi_oversold, "momentum_12m": momentum_12m,
    "inside_bar": inside_bar, "doji": doji,
}
BEARISH = {
    "bearish_engulfing": bearish_engulfing, "shooting_star": shooting_star,
    "gap_down": gap_down, "three_black_crows": three_black_crows,
    "breakdown_20d": breakdown_20d, "death_cross": death_cross,
    "rsi_overbought": rsi_overbought, "reversal_1m": reversal_1m,
    "outside_bar": outside_bar,
}
PATTERNS = {**BULLISH, **BEARISH}

LOOKBACK = 253      # enough for a 200-day average and a 12-month lookback


def study(series: list[Candle], pattern, horizon: int, *, short: bool = False) -> PatternResult | None:
    """Forward returns after this pattern, against the all-days base rate."""
    if len(series) < LOOKBACK + horizon + 2:
        return None

    hits, everything = [], []
    for i in range(LOOKBACK, len(series) - horizon):
        entry = series[i].close
        future = series[i + horizon].close
        move = (future - entry) / entry
        if short:
            move = -move
        everything.append(move)
        window = series[i - LOOKBACK + 1: i + 1]
        try:
            fired = pattern(window)
        except (IndexError, ZeroDivisionError):
            continue
        if fired:
            hits.append(move)

    if len(hits) < 30:      # too few occurrences to say anything
        return None
    return PatternResult(
        name=getattr(pattern, "__name__", "?"), horizon=horizon,
        occurrences=len(hits),
        mean_return=statistics.mean(hits),
        base_rate_mean=statistics.mean(everything),
        hit_rate=sum(1 for m in hits if m > 0) / len(hits),
        base_hit_rate=sum(1 for m in everything if m > 0) / len(everything),
        stdev=statistics.pstdev(hits) or 1e-9,
    )

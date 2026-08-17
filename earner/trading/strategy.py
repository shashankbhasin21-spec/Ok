"""Strategies and the indicators they read.

Every strategy answers the same nine questions the spec demands (§2) in a
structured `Signal`: what the edge is, how confident, where the stop goes,
what invalidates it. A strategy that cannot state its stop is not a strategy.

No signal is ever produced from a single indicator (§6). Each one requires a
confluence, and each returns None far more often than it returns a trade —
"no setup" is the correct answer most of the time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .broker import BUY, SELL


@dataclass
class Candle:
    at: float
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Signal:
    symbol: str
    side: str
    strategy: str
    confidence: float          # 0-1
    entry: float
    stop: float
    target: float
    thesis: str
    invalidation: str
    edge: str = ""
    metrics: dict = field(default_factory=dict)

    @property
    def risk_per_share(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_per_share(self) -> float:
        return abs(self.target - self.entry)

    @property
    def reward_to_risk(self) -> float:
        return self.reward_per_share / self.risk_per_share if self.risk_per_share else 0.0

    def expected_value(self, win_probability: float, cost_per_share: float = 0.0) -> float:
        """EV per share (spec §11). Negative EV must never be traded."""
        return (
            win_probability * self.reward_per_share
            - (1 - win_probability) * self.risk_per_share
            - cost_per_share
        )


# ── bars versus time ────────────────────────────────────────────────────────
#
# The distinction that this section exists to enforce: a *bar count* and a
# *wall-clock duration* are different quantities, and confusing them silently
# changes what a strategy does.
#
# `opening_range(candles, 15)` used to slice `candles[:15]` — fifteen bars. On
# one-minute data that is fifteen minutes and correct. On five-minute data it is
# seventy-five minutes, while the log still said "the 15m high". Every result
# measured on five-minute bars was therefore for a strategy nobody described.
#
# Strategies now declare durations in minutes and convert against the interval
# of the data they are actually handed, so a 15-minute opening range is fifteen
# minutes on every timeframe.

class TimeframeError(ValueError):
    """A duration cannot be expressed in whole bars of this interval."""


def infer_bar_minutes(candles: list[Candle]) -> int:
    """The bar interval of this series, in minutes, from the timestamps.

    Read from the data rather than configured, because a configured value can
    disagree with the file it describes and nothing would notice.
    """
    if len(candles) < 2:
        raise TimeframeError("need at least two bars to infer an interval")
    gaps = [candles[i + 1].at - candles[i].at for i in range(len(candles) - 1)]
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        raise TimeframeError("bars carry no usable timestamps")
    gaps.sort()
    # Median, so an overnight gap between sessions does not set the interval.
    seconds = gaps[len(gaps) // 2]
    minutes = round(seconds / 60)
    if minutes < 1:
        raise TimeframeError(f"sub-minute bars ({seconds:.0f}s) are not supported")
    return minutes


def bars_for(duration_minutes: int, bar_minutes: int) -> int:
    """How many bars of `bar_minutes` make up `duration_minutes`.

    Refuses rather than rounds. A 15-minute range on 7-minute bars is not two
    bars and not three; it is a request that cannot be honoured, and silently
    rounding it would reintroduce exactly the bug this replaces.
    """
    if duration_minutes <= 0 or bar_minutes <= 0:
        raise TimeframeError("durations and intervals must be positive")
    if duration_minutes % bar_minutes:
        raise TimeframeError(
            f"{duration_minutes}-minute window is not a whole number of "
            f"{bar_minutes}-minute bars"
        )
    return duration_minutes // bar_minutes


def warmed_up(candles: list[Candle], minutes: int | None = None) -> bool:
    """Enough history for the indicators, measured in time rather than bars.

    The bug this replaces: `len(candles) < 40` meant forty minutes live on
    one-minute bars and two hundred minutes in a five-minute backtest, so the
    strategy that was tested was not the strategy that would run.
    """
    if len(candles) < 2:
        return False
    try:
        need = bars_for(minutes or WARMUP_MINUTES, infer_bar_minutes(candles))
    except TimeframeError:
        return False
    # Indicator periods are bar counts by convention; EMA21 + ATR14 needs 22.
    return len(candles) >= max(need, 22)


# The history every strategy needs before it may speak, as a duration rather
# than a bar count: EMA21 plus ATR14 on one-minute bars. Converted per
# timeframe at evaluation time so backtest and live warm up for the same
# wall-clock period rather than the same number of bars.
WARMUP_MINUTES = 40


# ── indicators ──────────────────────────────────────────────────────────────

def ready(*values) -> bool:
    """True when every indicator returned a number.

    Tested with ``is not None`` rather than truthiness on purpose: RSI 0.0 is a
    legitimate reading — maximal oversold, which is exactly what mean reversion
    is looking for — and a falsy check would silently discard it.
    """
    return all(v is not None for v in values)


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    out = sum(values[:period]) / period
    for v in values[period:]:
        out = v * k + out * (1 - k)
    return out


def vwap(candles: list[Candle]) -> float | None:
    volume = sum(c.volume for c in candles)
    if not volume:
        return None
    return sum(((c.high + c.low + c.close) / 3) * c.volume for c in candles) / volume


def atr(candles: list[Candle], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    ranges = []
    for prev, cur in zip(candles[-period - 1:-1], candles[-period:]):
        ranges.append(max(
            cur.high - cur.low,
            abs(cur.high - prev.close),
            abs(cur.low - prev.close),
        ))
    return sum(ranges) / len(ranges)


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains = losses = 0.0
    for prev, cur in zip(values[-period - 1:-1], values[-period:]):
        change = cur - prev
        gains += max(change, 0)
        losses += max(-change, 0)
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100 - (100 / (1 + rs))


def opening_range(candles: list[Candle], duration_minutes: int = 15,
                  bar_minutes: int | None = None) -> tuple[float, float] | None:
    """High and low of the first `duration_minutes` of the session.

    `bar_minutes` is inferred from the series when not given, so the window is
    a real duration on any timeframe rather than a bar count that happens to
    equal one on one-minute data.
    """
    if len(candles) < 2:
        return None
    bar_minutes = bar_minutes or infer_bar_minutes(candles)
    bars = bars_for(duration_minutes, bar_minutes)
    if len(candles) < bars:
        return None
    window = candles[:bars]
    return max(c.high for c in window), min(c.low for c in window)


# ── strategies ──────────────────────────────────────────────────────────────

class Strategy:
    name = "base"
    regimes: tuple[str, ...] = ()

    def evaluate(self, symbol: str, candles: list[Candle]) -> Signal | None:
        raise NotImplementedError


class OpeningRangeBreakout(Strategy):
    """Break of the first 15 minutes' range, confirmed by volume and VWAP.

    Requires three things to agree, because a range break on thin volume is
    noise and reverses more often than it continues.
    """

    name = "opening_range_breakout"
    regimes = ("STRONG_BULL", "BULL", "STRONG_BEAR", "BEAR")

    def __init__(self, range_minutes: int = 15, volume_multiple: float = 1.5):
        # A wall-clock duration, converted to bars against whatever data arrives.
        self.range_minutes = range_minutes
        self.volume_multiple = volume_multiple

    def evaluate(self, symbol: str, candles: list[Candle]) -> Signal | None:
        if len(candles) < 2:
            return None
        try:
            bar_minutes = infer_bar_minutes(candles)
            range_bars = bars_for(self.range_minutes, bar_minutes)
            warmup_bars = max(bars_for(WARMUP_MINUTES, bar_minutes), range_bars + 2)
        except TimeframeError:
            return None

        rng = opening_range(candles, self.range_minutes, bar_minutes)
        current_atr = atr(candles)
        current_vwap = vwap(candles)
        if rng is None or not ready(current_atr, current_vwap) or len(candles) < warmup_bars:
            return None
        if not current_atr:
            return None  # a zero-range tape has no stop to place

        high, low = rng
        last = candles[-1]
        # Compare like with like: the recent window and the baseline window are
        # both durations, so the volume ratio means the same thing on any
        # timeframe.
        recent_bars = max(bars_for(15, bar_minutes), 1) if bar_minutes <= 15 else 1
        recent_volume = sum(c.volume for c in candles[-recent_bars:]) / recent_bars
        baseline = sum(c.volume for c in candles[:range_bars]) / range_bars
        if baseline and recent_volume < baseline * self.volume_multiple:
            return None  # break without participation

        if last.close > high and last.close > current_vwap:
            stop = max(high - current_atr * 0.5, low)
            return Signal(
                symbol=symbol, side=BUY, strategy=self.name,
                confidence=min(0.75, 0.5 + (recent_volume / max(baseline, 1) - 1) * 0.1),
                entry=last.close, stop=stop, target=last.close + (last.close - stop) * 2,
                edge="Range breaks on expanding volume continue more often than they revert.",
                thesis=f"Closed {last.close:.2f} above the {self.range_minutes}m high "
                       f"{high:.2f} and above VWAP {current_vwap:.2f} on "
                       f"{recent_volume / max(baseline, 1):.1f}x volume.",
                invalidation=f"Loss of {stop:.2f} means the break failed.",
                metrics={"or_high": high, "or_low": low, "vwap": current_vwap, "atr": current_atr},
            )

        if last.close < low and last.close < current_vwap:
            stop = min(low + current_atr * 0.5, high)
            return Signal(
                symbol=symbol, side=SELL, strategy=self.name,
                confidence=min(0.75, 0.5 + (recent_volume / max(baseline, 1) - 1) * 0.1),
                entry=last.close, stop=stop, target=last.close - (stop - last.close) * 2,
                edge="Range breaks on expanding volume continue more often than they revert.",
                thesis=f"Closed {last.close:.2f} below the {self.range_minutes}m low "
                       f"{low:.2f} and below VWAP {current_vwap:.2f}.",
                invalidation=f"Reclaim of {stop:.2f} means the break failed.",
                metrics={"or_high": high, "or_low": low, "vwap": current_vwap, "atr": current_atr},
            )
        return None


class VWAPMomentum(Strategy):
    """Trend continuation: price holding above VWAP with EMAs aligned."""

    name = "vwap_momentum"
    regimes = ("STRONG_BULL", "BULL", "BEAR", "STRONG_BEAR")

    def evaluate(self, symbol: str, candles: list[Candle]) -> Signal | None:
        if not warmed_up(candles):
            return None
        closes = [c.close for c in candles]
        current_vwap, fast, slow = vwap(candles), ema(closes, 9), ema(closes, 21)
        current_atr, strength = atr(candles), rsi(closes)
        if not ready(current_vwap, fast, slow, current_atr, strength):
            return None

        last = candles[-1].close
        # Three-way confluence, plus RSI as an overextension filter.
        if last > current_vwap and fast > slow and 50 < strength < 72:
            stop = last - current_atr * 1.2
            return Signal(
                symbol=symbol, side=BUY, strategy=self.name,
                confidence=min(0.7, 0.45 + (strength - 50) / 100),
                entry=last, stop=stop, target=last + (last - stop) * 1.8,
                edge="Intraday trends persist while price holds VWAP with EMAs aligned.",
                thesis=f"{last:.2f} above VWAP {current_vwap:.2f}, EMA9 {fast:.2f} > "
                       f"EMA21 {slow:.2f}, RSI {strength:.0f} — trending, not yet extended.",
                invalidation=f"Loss of VWAP or {stop:.2f} ends the trend thesis.",
                metrics={"vwap": current_vwap, "ema9": fast, "ema21": slow, "rsi": strength},
            )

        if last < current_vwap and fast < slow and 28 < strength < 50:
            stop = last + current_atr * 1.2
            return Signal(
                symbol=symbol, side=SELL, strategy=self.name,
                confidence=min(0.7, 0.45 + (50 - strength) / 100),
                entry=last, stop=stop, target=last - (stop - last) * 1.8,
                edge="Intraday trends persist while price holds VWAP with EMAs aligned.",
                thesis=f"{last:.2f} below VWAP {current_vwap:.2f}, EMA9 < EMA21, "
                       f"RSI {strength:.0f}.",
                invalidation=f"Reclaim of VWAP or {stop:.2f} ends the thesis.",
                metrics={"vwap": current_vwap, "ema9": fast, "ema21": slow, "rsi": strength},
            )
        return None


class MeanReversion(Strategy):
    """Fade a stretch away from VWAP — but only in a range, never in a trend."""

    name = "vwap_mean_reversion"
    regimes = ("RANGE", "NEUTRAL")

    def __init__(self, stretch_atr: float = 2.0):
        self.stretch_atr = stretch_atr

    def evaluate(self, symbol: str, candles: list[Candle]) -> Signal | None:
        if not warmed_up(candles):
            return None
        closes = [c.close for c in candles]
        current_vwap, current_atr, strength = vwap(candles), atr(candles), rsi(closes)
        if not ready(current_vwap, current_atr, strength) or not current_atr:
            return None

        last = candles[-1].close
        stretch = (last - current_vwap) / current_atr

        if stretch <= -self.stretch_atr and strength < 32:
            stop = last - current_atr * 1.0
            return Signal(
                symbol=symbol, side=BUY, strategy=self.name, confidence=0.55,
                entry=last, stop=stop, target=current_vwap,
                edge="In a range, price stretched far from VWAP tends to revert to it.",
                thesis=f"{abs(stretch):.1f} ATR below VWAP with RSI {strength:.0f} — "
                       "stretched, not trending.",
                invalidation="A close further from VWAP means this is a trend, not a stretch.",
                metrics={"stretch_atr": stretch, "vwap": current_vwap, "rsi": strength},
            )

        if stretch >= self.stretch_atr and strength > 68:
            stop = last + current_atr * 1.0
            return Signal(
                symbol=symbol, side=SELL, strategy=self.name, confidence=0.55,
                entry=last, stop=stop, target=current_vwap,
                edge="In a range, price stretched far from VWAP tends to revert to it.",
                thesis=f"{stretch:.1f} ATR above VWAP with RSI {strength:.0f}.",
                invalidation="A close further from VWAP means this is a trend, not a stretch.",
                metrics={"stretch_atr": stretch, "vwap": current_vwap, "rsi": strength},
            )
        return None


def classify_regime(candles: list[Candle]) -> str:
    """Market regime (spec §01). Strategies are only run in regimes that suit them."""
    if not warmed_up(candles):
        return "NEUTRAL"
    closes = [c.close for c in candles]
    fast, slow, current_atr = ema(closes, 9), ema(closes, 21), atr(candles)
    if not ready(fast, slow, current_atr):
        return "NEUTRAL"

    drift = (fast - slow) / slow if slow else 0
    volatility = current_atr / closes[-1] if closes[-1] else 0

    if volatility > 0.02:
        return "EXTREME_VOLATILITY"
    if drift > 0.004:
        return "STRONG_BULL"
    if drift > 0.0015:
        return "BULL"
    if drift < -0.004:
        return "STRONG_BEAR"
    if drift < -0.0015:
        return "BEAR"
    return "RANGE"


ALL_STRATEGIES = (OpeningRangeBreakout(), VWAPMomentum(), MeanReversion())

"""Which market you are in, and what time of day it is.

Two classifiers the brief asks for separately (§12, §13) and which belong
together, because in an Indian intraday session they are not independent: the
first thirty minutes is high-volatility and trending by construction, the
middle of the day is a range by construction, and a regime label that ignores
the clock will keep rediscovering the clock.

The regime states are the brief's ten. Three of them — PANIC, LIQUIDITY_SHOCK,
EXHAUSTION — are deliberately rare and are defined by thresholds that a normal
session does not reach, because a regime label that fires every day is a label
with no information in it. The distribution each state actually reaches on real
data is measured in `docs/RAPID_ENGINE.md` rather than assumed here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from earner.trading.risk import IST

STRONG_TREND = "STRONG_TREND"
WEAK_TREND = "WEAK_TREND"
RANGE = "RANGE"
BREAKOUT = "BREAKOUT"
HIGH_VOLATILITY = "HIGH_VOLATILITY"
LOW_VOLATILITY = "LOW_VOLATILITY"
PANIC = "PANIC"
LIQUIDITY_SHOCK = "LIQUIDITY_SHOCK"
EXHAUSTION = "EXHAUSTION"
REVERSAL = "REVERSAL"

# Which setups are permitted to fire in which regime. The mapping is the
# brief's, and it is data rather than code so that the ablation study can
# switch it off and measure what it was worth.
REGIME_SETUPS = {
    STRONG_TREND: {"momentum_burst", "trend_continuation", "breakout"},
    WEAK_TREND: {"trend_continuation"},
    RANGE: {"rapid_reversal"},
    BREAKOUT: {"momentum_burst", "breakout"},
    HIGH_VOLATILITY: {"breakout_failure", "rapid_reversal"},
    LOW_VOLATILITY: set(),
    PANIC: set(),
    LIQUIDITY_SHOCK: set(),
    EXHAUSTION: {"rapid_reversal", "breakout_failure"},
    REVERSAL: {"rapid_reversal"},
}

OPEN_SECONDS = 9 * 3600 + 15 * 60
CLOSE_SECONDS = 15 * 3600 + 30 * 60

# The five buckets of §12. Boundaries are the session's own structure — the
# opening auction's overhang, the pre-lunch drift, the European open around
# 12:30 IST, and the last half hour when intraday positions are squared off.
BUCKETS = (
    ("OPEN", 9 * 3600 + 15 * 60, 9 * 3600 + 45 * 60),
    ("EARLY", 9 * 3600 + 45 * 60, 11 * 3600 + 30 * 60),
    ("MID", 11 * 3600 + 30 * 60, 13 * 3600 + 30 * 60),
    ("AFTERNOON", 13 * 3600 + 30 * 60, 15 * 3600),
    ("CLOSE", 15 * 3600, 15 * 3600 + 30 * 60),
)


def bucket(when: datetime | float) -> str:
    """Which part of the session a timestamp falls in."""
    if isinstance(when, (int, float)):
        when = datetime.fromtimestamp(when, IST)
    seconds = when.hour * 3600 + when.minute * 60
    for name, start, end in BUCKETS:
        if start <= seconds < end:
            return name
    return "OUTSIDE"


def minutes_into_session(when: datetime | float) -> int:
    if isinstance(when, (int, float)):
        when = datetime.fromtimestamp(when, IST)
    return (when.hour * 3600 + when.minute * 60 - OPEN_SECONDS) // 60


def minutes_to_close(when: datetime | float) -> int:
    if isinstance(when, (int, float)):
        when = datetime.fromtimestamp(when, IST)
    return (CLOSE_SECONDS - when.hour * 3600 - when.minute * 60) // 60


@dataclass
class Regime:
    state: str
    trend_strength: float      # |drift| in units of bar volatility
    volatility_z: float
    liquidity_z: float
    permitted: set

    def allows(self, setup: str) -> bool:
        return setup in self.permitted


def _returns(bars: list) -> list[float]:
    out = []
    for i in range(1, len(bars)):
        if bars[i - 1].close > 0:
            out.append(math.log(bars[i].close / bars[i - 1].close))
    return out


def _stdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def classify(bars: list, *, window: int = 40, flow=None) -> Regime:
    """The market's current state, from price, volatility and volume.

    Order of tests is the order of severity: a liquidity shock is a liquidity
    shock whatever the trend is doing, so it is checked first. Getting this
    order wrong is how a panic gets labelled a strong downtrend and traded as
    momentum.
    """
    if len(bars) < 12:
        return Regime(LOW_VOLATILITY, 0.0, 0.0, 0.0, set())

    recent = bars[-window:] if len(bars) > window else bars
    rets = _returns(recent)
    if len(rets) < 6:
        return Regime(LOW_VOLATILITY, 0.0, 0.0, 0.0, set())

    sigma = _stdev(rets)
    short = rets[-6:]
    short_sigma = _stdev(short)
    drift = sum(short) / len(short)
    strength = abs(drift) / sigma if sigma > 0 else 0.0

    volumes = [b.volume for b in recent]
    baseline = sum(volumes[:-6]) / max(len(volumes) - 6, 1)
    latest = sum(volumes[-3:]) / 3
    liquidity_z = (latest - baseline) / baseline if baseline > 0 else 0.0
    volatility_z = (short_sigma / sigma - 1) if sigma > 0 else 0.0

    # Liquidity shock: volume collapses while the range does not. The book has
    # emptied and prices are moving on nothing. Never a state to trade in.
    if liquidity_z < -0.6 and volatility_z > 0.3:
        state = LIQUIDITY_SHOCK
    # Panic: volatility trebles with heavy one-sided selling.
    elif volatility_z > 1.5 and drift < 0 and liquidity_z > 0.8:
        state = PANIC
    elif flow is not None and flow.exhaustion > 1.5:
        state = EXHAUSTION
    elif flow is not None and flow.imbalance_run >= 4 and strength < 0.5:
        state = REVERSAL
    elif volatility_z > 0.8 and liquidity_z > 0.5 and strength > 1.0:
        state = BREAKOUT
    elif volatility_z > 0.8:
        state = HIGH_VOLATILITY
    elif strength > 1.2:
        state = STRONG_TREND
    elif strength > 0.6:
        state = WEAK_TREND
    elif short_sigma < sigma * 0.5:
        state = LOW_VOLATILITY
    else:
        state = RANGE

    return Regime(state, strength, volatility_z, liquidity_z,
                  set(REGIME_SETUPS.get(state, set())))

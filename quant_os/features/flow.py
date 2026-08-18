"""Order flow, reconstructed from bars — and the honest name for that.

The brief asks for aggressive buy volume, aggressive sell volume, cumulative
delta and trade-size distribution. Those quantities are defined on the trade
tape: each print carries a price, a size and — by comparison with the quote at
that instant — a direction. This repository's historical feed carries open,
high, low, close and volume, and nothing else. The audit recorded that:

    FIELDS AVAILABLE : open high low close volume
    FIELDS REQUIRED  : bid ABSENT · ask ABSENT · depth ABSENT
                       trade_direction ABSENT · level2 ABSENT · tick ABSENT

So true order flow cannot be computed here. What *can* be computed is a set of
estimators that recover the direction split from the bar's shape, and the
literature on them is old and specific:

* **Close location value** (Chaikin, 1980s) splits the bar's volume by where
  the close sits inside the range. It is exact when the bar is one trade and
  degrades smoothly as the bar contains more.
* **The tick rule** (Lee & Ready 1991) signs volume by the direction of the
  price change. At bar frequency it is a coarse instrument: Easley, López de
  Prado & O'Hara measured bulk-volume classification against the true tape and
  found agreement in the 70-80% range on liquid futures.

Seventy-five percent accurate direction is not order flow. It is a noisy
observation of order flow, and a signal built on it inherits the noise. Every
function here is named `_proxy` in its docstring and the field names carry the
word, because the single most expensive thing this module could do is let a
later reader believe these numbers came off a tape.

What it means practically: a feature here is worth using if it survives
validation *despite* being 75% accurate. If an edge needs the other 25%, it is
not available from this data, and the depth recorder is the only route to it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Bar:
    """The subset of a candle these estimators need."""

    at: float
    open: float
    high: float
    low: float
    close: float
    volume: float


def close_location(bar) -> float:
    """Where the close sits in the range, on [-1, +1]. +1 is a close on the high.

    The workhorse. A bar that closes on its high spent the interval being
    lifted; a bar that closes on its low spent it being hit. On a doji — high
    equal to low — the value is 0, which is the correct answer: no information.
    """
    span = bar.high - bar.low
    if span <= 0:
        return 0.0
    return ((bar.close - bar.low) - (bar.high - bar.close)) / span


def split_volume(bar) -> tuple[float, float]:
    """(buy_proxy, sell_proxy) — the bar's volume split by close location.

    Not aggressive buy and sell volume. An *estimate* of the split, exact only
    in the degenerate case, and biased whenever the bar's path differs from its
    shape — a bar that rallies then fades ends mid-range and is scored neutral
    though it contained a full round trip of aggression.
    """
    clv = close_location(bar)
    return bar.volume * (1 + clv) / 2, bar.volume * (1 - clv) / 2


def signed_volume(bar, previous=None) -> float:
    """Volume signed by direction — the tick rule, at bar resolution.

    Uses close-to-close when a previous bar exists, and falls back to the
    bar's own body when it does not. Where the two estimators disagree the
    bar is genuinely ambiguous, which `agreement()` below exposes rather than
    hides.
    """
    if previous is not None and previous.close != bar.close:
        direction = 1.0 if bar.close > previous.close else -1.0
    elif bar.close != bar.open:
        direction = 1.0 if bar.close > bar.open else -1.0
    else:
        direction = 0.0
    return bar.volume * direction


def agreement(bars: list) -> float:
    """Fraction of bars where the two direction estimators agree.

    A diagnostic, not a feature. When it drops the flow features are noise,
    and knowing that is worth more than any of them.
    """
    if len(bars) < 2:
        return 0.0
    same = 0
    for i in range(1, len(bars)):
        a = close_location(bars[i])
        b = signed_volume(bars[i], bars[i - 1])
        if a == 0 or b == 0:
            continue
        same += (a > 0) == (b > 0)
    return same / (len(bars) - 1)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def zscore(value: float, history: list[float]) -> float:
    """Standard scores, clipped. An unclipped z-score of 40 on a thin bar
    dominates any linear model it is fed to; the information is 'very large',
    and beyond about six deviations the exact number is noise."""
    sd = _stdev(history)
    if sd <= 0:
        return 0.0
    return max(-6.0, min(6.0, (value - _mean(history)) / sd))


@dataclass
class FlowState:
    """Order-flow proxies over a rolling window. Every field is an estimate."""

    delta: float = 0.0                 # cumulative signed volume, session
    delta_proxy: float = 0.0           # cumulative CLV-weighted imbalance
    imbalance: float = 0.0             # last bar's buy share, on [-1, +1]
    imbalance_run: int = 0             # consecutive bars of one-sided pressure
    volume_z: float = 0.0              # volume relative to its own recent mean
    volume_acceleration: float = 0.0   # change in volume z, bar over bar
    range_z: float = 0.0
    absorption: float = 0.0            # heavy volume, small range
    exhaustion: float = 0.0            # heavy volume, wide range, weak close
    burst: bool = False                # volume expansion plus one-sided flow
    trade_size_z: float = 0.0          # volume per unit of range: size proxy
    agreement: float = 0.0
    features: dict = field(default_factory=dict)


def flow_state(bars: list, *, window: int = 40) -> FlowState:
    """Every order-flow proxy the bar data supports, for the latest bar.

    `window` is in bars. Forty five-minute bars is a little over three hours,
    which is the longest window that still fits inside one Indian session and
    therefore the longest that does not silently compare this morning against
    yesterday afternoon.
    """
    if len(bars) < 5:
        return FlowState()

    recent = bars[-window:] if len(bars) > window else bars
    history = recent[:-1]
    bar = bars[-1]

    volumes = [b.volume for b in history]
    ranges = [b.high - b.low for b in history]
    span = bar.high - bar.low

    buy, sell = split_volume(bar)
    total = buy + sell
    imbalance = (buy - sell) / total if total else 0.0

    delta = sum(signed_volume(b, bars[i - 1] if i else None)
                for i, b in enumerate(recent))
    delta_proxy = sum((lambda p: p[0] - p[1])(split_volume(b)) for b in recent)

    run, sign = 0, (1 if imbalance > 0 else -1 if imbalance < 0 else 0)
    if sign:
        for b in reversed(recent):
            bi = close_location(b)
            if (bi > 0) == (sign > 0) and bi != 0:
                run += 1
            else:
                break

    volume_z = zscore(bar.volume, volumes)
    previous_z = zscore(history[-1].volume, volumes[:-1]) if len(volumes) > 2 else 0.0
    range_z = zscore(span, ranges)

    # Absorption: the market took size without moving. Heavy volume into a
    # narrow range means resting liquidity met aggression and won.
    absorption = volume_z - range_z if volume_z > 0.5 else 0.0

    # Exhaustion: heavy volume, a wide range, and a close that gave most of it
    # back. The aggressor spent everything and finished where it started.
    exhaustion = 0.0
    if volume_z > 1.0 and range_z > 0.5:
        exhaustion = (volume_z + range_z) / 2 * (1 - abs(close_location(bar)))

    size_proxy = bar.volume / span if span > 0 else 0.0
    sizes = [b.volume / (b.high - b.low) for b in history if b.high > b.low]

    return FlowState(
        delta=delta,
        delta_proxy=delta_proxy,
        imbalance=imbalance,
        imbalance_run=run,
        volume_z=volume_z,
        volume_acceleration=volume_z - previous_z,
        range_z=range_z,
        absorption=absorption,
        exhaustion=exhaustion,
        burst=volume_z > 1.5 and abs(imbalance) > 0.4,
        trade_size_z=zscore(size_proxy, sizes),
        agreement=agreement(recent),
        features={
            "buy_proxy": buy, "sell_proxy": sell,
            "clv": close_location(bar), "span": span,
        },
    )


def divergence(bars: list, *, lookback: int = 10) -> float:
    """Price up while flow deteriorates, or the reverse. Signed, roughly [-2, 2].

    The brief's exhaustion pattern in one number: price making a new extreme
    that the flow proxy does not confirm. Positive means price rose without
    buying pressure behind it — the bearish divergence. Negative is the mirror.

    Whether it predicts anything is a question for the validation stage, not
    for this function; it is computed here so that it can be *tested*, and the
    ablation study reports what happened when it was removed.
    """
    if len(bars) < lookback + 2:
        return 0.0
    window = bars[-lookback:]
    first, last = window[0], window[-1]
    if first.close <= 0:
        return 0.0
    price_move = (last.close - first.close) / first.close

    half = len(window) // 2
    early = sum((lambda p: p[0] - p[1])(split_volume(b)) for b in window[:half])
    late = sum((lambda p: p[0] - p[1])(split_volume(b)) for b in window[half:])
    scale = sum(b.volume for b in window) / 2 or 1.0
    flow_move = (late - early) / scale

    # Same sign: price and flow agree, no divergence. Opposite: divergence,
    # signed by the direction price went.
    #
    # `flow_move == 0` has to be excluded explicitly. Unchanged flow is not
    # flow moving against price — but `(price_move > 0) == (flow_move > 0)`
    # reads a zero as "negative" and reported a full-strength divergence on
    # every bar of a steadily trending series with constant pressure, which is
    # the one case where there is provably no divergence at all.
    if price_move == 0 or flow_move == 0:
        return 0.0
    if (price_move > 0) == (flow_move > 0):
        return 0.0
    return math.copysign(min(abs(price_move) * 100 + abs(flow_move), 2.0), price_move)

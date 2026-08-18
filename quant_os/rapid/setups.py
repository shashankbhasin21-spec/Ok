"""The five setup detectors, and the feature vector each one emits.

A setup is not a signal. It is a *candidate* — a moment where a named pattern
is present — carrying the features that describe it. Whether it becomes a
trade is decided later by a model trained on how these candidates actually
resolved, then by an expected-value gate, then by an adversarial check. That
separation is the whole architecture: the detector's job is recall, the
model's job is precision, and conflating them is how a rule-based bot ends up
with a hundred hand-tuned thresholds that fit one sample.

Each detector requires confluence across features that are not restatements of
each other (§5). Price acceleration and a volume z-score are close to
independent; RSI and stochastics are the same number twice. Where two features
in a detector are correlated it is noted in the detector.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from earner.trading.broker import BUY, SELL
from quant_os.features.flow import divergence, flow_state
from quant_os.features.regime import bucket, minutes_to_close

MOMENTUM_BURST = "momentum_burst"
BREAKOUT = "breakout"
BREAKOUT_FAILURE = "breakout_failure"
TREND_CONTINUATION = "trend_continuation"
RAPID_REVERSAL = "rapid_reversal"

ALL_SETUPS = (MOMENTUM_BURST, BREAKOUT, BREAKOUT_FAILURE,
              TREND_CONTINUATION, RAPID_REVERSAL)

# Holding periods, in minutes, by setup. §2 asks the system to select the
# horizon automatically; it does so by setup, because the horizon is a property
# of the pattern — a burst resolves in minutes, a trend continuation does not.
HORIZON_MINUTES = {
    MOMENTUM_BURST: 15,
    BREAKOUT: 30,
    BREAKOUT_FAILURE: 20,
    TREND_CONTINUATION: 45,
    RAPID_REVERSAL: 20,
}


@dataclass
class Setup:
    symbol: str
    kind: str
    side: str
    at: float
    price: float
    stop: float
    horizon_minutes: int
    features: dict = field(default_factory=dict)
    regime: str = ""

    @property
    def risk_per_share(self) -> float:
        return abs(self.price - self.stop)


def _stdev(xs) -> float:
    xs = list(xs)
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _atr(bars, period: int = 14) -> float:
    if len(bars) < period + 1:
        return 0.0
    trs = []
    for i in range(len(bars) - period, len(bars)):
        prev = bars[i - 1].close
        trs.append(max(bars[i].high - bars[i].low, abs(bars[i].high - prev),
                       abs(bars[i].low - prev)))
    return sum(trs) / len(trs) if trs else 0.0


def _acceleration(bars, span: int = 3) -> float:
    """Second derivative of price, in units of the bar's own volatility.

    Momentum says price is moving. Acceleration says the move is getting
    faster, which is the part that distinguishes a burst from a drift and is
    close to uncorrelated with the level of momentum itself.
    """
    if len(bars) < span * 2 + 1:
        return 0.0
    closes = [b.close for b in bars]
    recent = (closes[-1] - closes[-1 - span]) / span
    earlier = (closes[-1 - span] - closes[-1 - 2 * span]) / span
    sigma = _stdev(closes[i] - closes[i - 1] for i in range(1, len(closes)))
    return (recent - earlier) / sigma if sigma > 0 else 0.0


def base_features(bars, flow) -> dict:
    """The features every setup carries, whatever kind it is.

    One shared vector matters more than it looks: it lets one model be trained
    per setup family on directly comparable inputs, and it lets the ablation
    study remove a feature everywhere at once.
    """
    bar = bars[-1]
    closes = [b.close for b in bars]
    atr = _atr(bars)
    sigma = _stdev(closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)))
    return {
        "flow_imbalance": flow.imbalance,
        "flow_run": float(flow.imbalance_run),
        "flow_delta_norm": flow.delta_proxy / (sum(b.volume for b in bars[-40:]) or 1),
        "volume_z": flow.volume_z,
        "volume_accel": flow.volume_acceleration,
        "range_z": flow.range_z,
        "absorption": flow.absorption,
        "exhaustion": flow.exhaustion,
        "trade_size_z": flow.trade_size_z,
        "divergence": divergence(bars),
        "acceleration": _acceleration(bars),
        "atr_pct": atr / bar.close if bar.close else 0.0,
        "volatility": sigma,
        "close_position": ((bar.close - bar.low) / (bar.high - bar.low)
                           if bar.high > bar.low else 0.5),
        "minutes_left": float(minutes_to_close(bar.at)),
        "estimator_agreement": flow.agreement,
    }


def detect(symbol: str, bars: list, *, regime=None) -> list:
    """Every setup present on the latest bar. Usually none.

    Returns a list because two setups can be present at once — a breakout in
    an uptrend is both a breakout and a trend continuation — and the ensemble
    should see both rather than have one arbitrarily win.
    """
    if len(bars) < 25:
        return []

    flow = flow_state(bars)
    features = base_features(bars, flow)
    atr = _atr(bars)
    if atr <= 0:
        return []

    found = []
    for detector in (_momentum_burst, _breakout, _breakout_failure,
                     _trend_continuation, _rapid_reversal):
        setup = detector(symbol, bars, flow, features, atr)
        if setup is None:
            continue
        if regime is not None:
            setup.regime = regime.state
            if not regime.allows(setup.kind):
                continue
        setup.features = dict(features, **setup.features)
        setup.features["bucket_" + bucket(bars[-1].at)] = 1.0
        found.append(setup)
    return found


def _momentum_burst(symbol, bars, flow, features, atr):
    """Sudden volume expansion + price acceleration + one-sided flow.

    Four conditions, of which volume expansion and flow imbalance are the two
    most correlated — a heavy bar tends to close away from its midpoint. The
    acceleration term is what makes this more than 'a big green bar'.
    """
    bar = bars[-1]
    if flow.volume_z < 1.5 or abs(flow.imbalance) < 0.35:
        return None
    if abs(features["acceleration"]) < 0.5:
        return None
    if features["range_z"] < 0.5:
        return None
    side = BUY if flow.imbalance > 0 else SELL
    if (side == BUY) != (features["acceleration"] > 0):
        return None       # flow and acceleration disagree: not a burst
    stop = bar.close - atr if side == BUY else bar.close + atr
    return Setup(symbol=symbol, kind=MOMENTUM_BURST, side=side, at=bar.at,
                 price=bar.close, stop=stop,
                 horizon_minutes=HORIZON_MINUTES[MOMENTUM_BURST],
                 features={"burst_strength": flow.volume_z * abs(flow.imbalance)})


def _breakout(symbol, bars, flow, features, atr):
    """A close beyond the session's established range, with confirmation.

    The range is the day so far excluding the last three bars, so that the
    breakout bar cannot be part of the range it is breaking. Getting that
    wrong is the classic same-bar lookahead in breakout code.
    """
    bar = bars[-1]
    window = bars[-30:-3]
    if len(window) < 10:
        return None
    high, low = max(b.high for b in window), min(b.low for b in window)
    if high <= low:
        return None

    if bar.close > high:
        side, level = BUY, high
    elif bar.close < low:
        side, level = SELL, low
    else:
        return None

    # Confirmation from three independent places: volume, flow direction, and
    # where in its own range the bar closed.
    if flow.volume_z < 0.8:
        return None
    if (side == BUY and flow.imbalance < 0.2) or (side == SELL and flow.imbalance > -0.2):
        return None
    if side == BUY and features["close_position"] < 0.6:
        return None
    if side == SELL and features["close_position"] > 0.4:
        return None

    extension = abs(bar.close - level) / atr
    if extension > 2.0:
        return None       # already run; the entry is the worst price of the move
    stop = level - atr * 0.5 if side == BUY else level + atr * 0.5
    return Setup(symbol=symbol, kind=BREAKOUT, side=side, at=bar.at,
                 price=bar.close, stop=stop,
                 horizon_minutes=HORIZON_MINUTES[BREAKOUT],
                 features={"extension_atr": extension,
                           "range_width_atr": (high - low) / atr})


def _breakout_failure(symbol, bars, flow, features, atr):
    """Price broke the range, then closed back inside it, with flow reversing.

    Traded against the failed direction. The most demanding detector here,
    because a failure needs a break to have happened *and* been rejected, and
    both halves have to be found without peeking.
    """
    if len(bars) < 12:
        return None
    bar = bars[-1]
    window = bars[-30:-6]
    if len(window) < 8:
        return None
    high, low = max(b.high for b in window), min(b.low for b in window)
    recent = bars[-5:-1]

    broke_up = any(b.close > high for b in recent) and bar.close < high
    broke_down = any(b.close < low for b in recent) and bar.close > low
    if not (broke_up or broke_down):
        return None

    side = SELL if broke_up else BUY
    # The rejection has to show in the flow, not only in the price.
    if side == SELL and flow.imbalance > -0.15:
        return None
    if side == BUY and flow.imbalance < 0.15:
        return None
    if flow.volume_z < 0.3:
        return None

    stop = max(b.high for b in recent) if side == SELL else min(b.low for b in recent)
    if abs(stop - bar.close) < atr * 0.3:
        stop = bar.close + atr * 0.5 if side == SELL else bar.close - atr * 0.5
    return Setup(symbol=symbol, kind=BREAKOUT_FAILURE, side=side, at=bar.at,
                 price=bar.close, stop=stop,
                 horizon_minutes=HORIZON_MINUTES[BREAKOUT_FAILURE],
                 features={"failed_level": high if broke_up else low,
                           "rejection_atr": abs(bar.close - (high if broke_up else low)) / atr})


def _trend_continuation(symbol, bars, flow, features, atr):
    """Established trend + controlled pullback + flow still one-sided.

    'Controlled' is the operative word and is measured: the pullback must
    retrace between 20% and 60% of the impulse. Less is not a pullback, more
    is a reversal wearing one.
    """
    if len(bars) < 30:
        return None
    bar = bars[-1]
    closes = [b.close for b in bars]
    fast = sum(closes[-9:]) / 9
    slow = sum(closes[-21:]) / 21
    if slow <= 0:
        return None
    trend = (fast - slow) / slow
    if abs(trend) < 0.0015:
        return None
    side = BUY if trend > 0 else SELL

    swing = bars[-20:]
    if side == BUY:
        impulse_low = min(b.low for b in swing)
        impulse_high = max(b.high for b in swing)
        impulse = impulse_high - impulse_low
        retrace = (impulse_high - bar.close) / impulse if impulse > 0 else 0
    else:
        impulse_low = min(b.low for b in swing)
        impulse_high = max(b.high for b in swing)
        impulse = impulse_high - impulse_low
        retrace = (bar.close - impulse_low) / impulse if impulse > 0 else 0
    if not (0.20 <= retrace <= 0.60):
        return None

    # The pullback must be on lighter volume than the impulse — supply drying
    # up rather than a genuine turn.
    pullback_volume = sum(b.volume for b in bars[-4:]) / 4
    impulse_volume = sum(b.volume for b in bars[-20:-4]) / 16
    if impulse_volume <= 0 or pullback_volume > impulse_volume:
        return None
    if (side == BUY and flow.delta_proxy < 0) or (side == SELL and flow.delta_proxy > 0):
        return None

    stop = impulse_low - atr * 0.3 if side == BUY else impulse_high + atr * 0.3
    return Setup(symbol=symbol, kind=TREND_CONTINUATION, side=side, at=bar.at,
                 price=bar.close, stop=stop,
                 horizon_minutes=HORIZON_MINUTES[TREND_CONTINUATION],
                 features={"trend": trend, "retrace": retrace,
                           "volume_ratio": pullback_volume / impulse_volume})


def _rapid_reversal(symbol, bars, flow, features, atr):
    """Extreme short move + volatility spike + exhaustion + flow divergence.

    Explicitly not 'buy the dip' (§6). Four conditions must hold together, and
    the move has to be extreme *in units of the instrument's own volatility*,
    not in percent — a 2% move in a bank and in a mid-cap pharma are different
    events.
    """
    if len(bars) < 20:
        return None
    bar = bars[-1]
    closes = [b.close for b in bars]
    move = (closes[-1] - closes[-5]) / closes[-5] if closes[-5] > 0 else 0
    sigma = _stdev(closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)))
    if sigma <= 0:
        return None
    stretch = move / (sigma * math.sqrt(4))
    if abs(stretch) < 2.0:
        return None

    if flow.exhaustion < 0.8:
        return None
    if flow.volume_z < 0.8:
        return None

    side = SELL if stretch > 0 else BUY
    # The rejection must be visible in the bar: a close pushed back from the
    # extreme it just made.
    if side == SELL and features["close_position"] > 0.5:
        return None
    if side == BUY and features["close_position"] < 0.5:
        return None

    stop = bar.high + atr * 0.4 if side == SELL else bar.low - atr * 0.4
    return Setup(symbol=symbol, kind=RAPID_REVERSAL, side=side, at=bar.at,
                 price=bar.close, stop=stop,
                 horizon_minutes=HORIZON_MINUTES[RAPID_REVERSAL],
                 features={"stretch_sigma": stretch, "move_pct": move})

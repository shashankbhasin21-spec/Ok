"""From candidate to order — the gates, the tiers and the sizing (§9, §10, §19).

Four things stand between a detected setup and an order, in this order, and
the order is deliberate: each one is cheaper than the next, so the cheapest
rejection happens first.

    1. EXPECTED VALUE    arithmetic on the model's probability and the costs
    2. ADVERSARIAL CHECK nine questions, each answered from measured features
    3. TIER              A+ / A / B / C, from confidence and expectancy
    4. SIZE              risk-based, capped by margin, volatility and drawdown

The design rule underneath all of it: **the model proposes, arithmetic
disposes**. A probability is an input to a calculation, never a permission.
That is also the safety property the earlier briefs demanded — no language
model and no learned component can authorise an order on its own, because the
only thing that authorises an order is a positive number coming out of
`expected_value`, computed from costs that were looked up rather than
predicted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from earner.trading.broker import BUY
from quant_os.execution.costs import CostModel

A_PLUS, A, B, C = "A+", "A", "B", "C"

# Fraction of capital risked per trade, by tier. C is zero and is not a
# position size — it is the decision not to trade, kept in the same table so
# that the no-trade case is impossible to omit.
TIER_RISK = {A_PLUS: 0.010, A: 0.006, B: 0.003, C: 0.0}

# Minimum edge, as a multiple of the round-trip cost, for each tier. A trade
# whose expected value merely exceeds costs is a trade whose entire margin of
# safety is the accuracy of the cost model.
TIER_EDGE_MULTIPLE = {A_PLUS: 3.0, A: 2.0, B: 1.5}


@dataclass
class Decision:
    trade: bool
    tier: str
    quantity: int
    expected_value: float          # rupees per share, after all costs
    edge_multiple: float           # EV as a multiple of round-trip cost
    probability: float
    dispersion: float
    reasons: list = field(default_factory=list)   # why not, when not
    diagnostics: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.trade


def expected_value(*, probability: float, reward: float, risk: float,
                   cost_per_share: float) -> float:
    """EV per share. The one number allowed to authorise a trade.

    Note what is *not* here: no confidence multiplier, no "conviction boost",
    no scaling by how much the strategy wants the trade. Those are the knobs
    that turn a negative expectancy positive on a slide.
    """
    return probability * reward - (1 - probability) * risk - cost_per_share


def adversarial(setup, prediction, *, recent_failures: int = 0,
                estimator_agreement: float = 1.0) -> list:
    """§19, as nine measurable questions. Returns the reasons to stand down.

    Every question is answered from a feature that was computed before the
    trade, not from a judgement. A checklist whose items are answered by
    opinion is a checklist that always passes.
    """
    f = setup.features
    objections = []

    # Is this a false breakout?
    if setup.kind in ("breakout", "momentum_burst") and f.get("extension_atr", 0) > 1.5:
        objections.append("entry is >1.5 ATR beyond the level: the move is already paid for")

    # Is liquidity disappearing?
    if f.get("volume_z", 0) < -0.5:
        objections.append("volume below its recent mean: taking liquidity that is not there")

    # Is the move exhausted?
    if f.get("exhaustion", 0) > 1.5 and setup.kind in ("momentum_burst", "breakout",
                                                       "trend_continuation"):
        objections.append("exhaustion signature on a continuation setup")

    # Is order flow reversing?
    imbalance = f.get("flow_imbalance", 0.0)
    if (setup.side == BUY and imbalance < -0.2) or (setup.side != BUY and imbalance > 0.2):
        objections.append("flow proxy points against the trade")

    # Is volatility abnormal?
    if f.get("atr_pct", 0) > 0.02:
        objections.append("ATR above 2% of price: stop distance is not survivable at size")

    # Are fees destroying the edge? (answered by the caller's EV; repeated here
    # so the check is complete on its own)
    if prediction.get("expected_return", 0.0) <= 0:
        objections.append("the return model expects a loss")

    # Is the model overconfident?
    if prediction.get("dispersion", 0.0) > 0.12:
        objections.append("heads disagree: dispersion above 0.12")

    # Has this setup recently failed?
    if recent_failures >= 3:
        objections.append(f"{recent_failures} consecutive failures of this setup today")

    # Is the direction estimate itself trustworthy?
    if estimator_agreement < 0.6:
        objections.append("direction estimators agree on <60% of recent bars")

    # Squaring off into the close is a forced exit at whatever price exists.
    if f.get("minutes_left", 999) < setup.horizon_minutes + 10:
        objections.append("not enough session left to hold the intended horizon")

    return objections


def tier_for(edge_multiple: float, probability: float, dispersion: float) -> str:
    """A+ / A / B / C. Requires both a large edge and agreement among heads."""
    if edge_multiple >= TIER_EDGE_MULTIPLE[A_PLUS] and probability >= 0.55 and dispersion < 0.06:
        return A_PLUS
    if edge_multiple >= TIER_EDGE_MULTIPLE[A] and probability >= 0.45 and dispersion < 0.09:
        return A
    if edge_multiple >= TIER_EDGE_MULTIPLE[B] and probability >= 0.40:
        return B
    return C


def size_for(tier: str, *, capital: float, risk_per_share: float, price: float,
             max_leverage: float = 5.0, max_positions: int = 5,
             drawdown_fraction: float = 0.0, bar_volume: float = 0.0,
             participation_cap: float = 0.05) -> int:
    """Shares to trade. Every clause here reduces the number; none increases it.

    That asymmetry is the whole point. `drawdown_fraction` shrinks size as the
    account falls, which is the opposite of a martingale and is the behaviour
    §10 asks for explicitly. Nothing in this function can respond to a loss by
    trading larger.
    """
    if tier == C or risk_per_share <= 0 or price <= 0:
        return 0
    risk_budget = capital * TIER_RISK[tier]

    # Drawdown taper: at a 10% drawdown the size is halved, at 20% it is zero.
    taper = max(0.0, 1.0 - drawdown_fraction / 0.20)
    risk_budget *= taper
    quantity = int(risk_budget / risk_per_share)

    # Per-position margin ceiling, so one name cannot eat the book.
    margin_cap = int((capital * max_leverage / max_positions) / price)
    quantity = min(quantity, margin_cap)

    # Participation ceiling: never more than a slice of the bar's own volume.
    if bar_volume > 0:
        quantity = min(quantity, int(bar_volume * participation_cap))
    return max(quantity, 0)


def decide(setup, prediction, *, capital: float, costs: CostModel,
           spread: float, bar_volume: float, volatility: float,
           reward_to_risk: float = 2.0, drawdown_fraction: float = 0.0,
           recent_failures: int = 0, max_positions: int = 5) -> Decision:
    """The full pipeline for one candidate. Returns a Decision, never an order.

    Deliberately returns a value rather than placing anything: the caller —
    backtest or live engine — is the only thing that talks to a broker, and
    keeping that boundary means the identical decision logic is exercised in
    both without a flag that changes behaviour.
    """
    risk = setup.risk_per_share
    if risk <= 0:
        return Decision(False, C, 0, 0.0, 0.0, prediction.get("p_win", 0.0),
                        prediction.get("dispersion", 0.0), ["setup has no stop distance"])

    reward = risk * reward_to_risk
    probability = prediction["p_win"]

    # Size is needed to price the trade, and the price of the trade decides the
    # size. Resolved by sizing at the B tier first, then re-checking: an
    # optimistic first pass would understate cost per share.
    provisional = size_for(B, capital=capital, risk_per_share=risk, price=setup.price,
                           max_positions=max_positions,
                           drawdown_fraction=drawdown_fraction, bar_volume=bar_volume)
    if provisional <= 0:
        return Decision(False, C, 0, 0.0, 0.0, probability,
                        prediction.get("dispersion", 0.0), ["position size rounds to zero"])

    cost_per_share = costs.per_share_cost(setup.price, provisional, spread=spread,
                                          bar_volume=bar_volume, volatility=volatility)
    ev = expected_value(probability=probability, reward=reward, risk=risk,
                        cost_per_share=cost_per_share)
    edge_multiple = ev / cost_per_share if cost_per_share > 0 else 0.0

    reasons = []
    if ev <= 0:
        reasons.append(f"expected value {ev:+.3f}/share after costs of {cost_per_share:.3f}")

    objections = adversarial(setup, prediction, recent_failures=recent_failures,
                             estimator_agreement=setup.features.get("estimator_agreement", 1.0))
    reasons.extend(objections)

    tier = tier_for(edge_multiple, probability, prediction.get("dispersion", 0.0))
    if tier == C:
        reasons.append(f"edge multiple {edge_multiple:.2f} below tier B threshold")

    diagnostics = {"cost_per_share": cost_per_share, "reward": reward, "risk": risk,
                   "edge_multiple": edge_multiple, "provisional_qty": provisional}

    if reasons:
        return Decision(False, C, 0, ev, edge_multiple, probability,
                        prediction.get("dispersion", 0.0), reasons, diagnostics)

    quantity = size_for(tier, capital=capital, risk_per_share=risk, price=setup.price,
                        max_positions=max_positions,
                        drawdown_fraction=drawdown_fraction, bar_volume=bar_volume)
    if quantity <= 0:
        return Decision(False, C, 0, ev, edge_multiple, probability,
                        prediction.get("dispersion", 0.0), ["size rounds to zero at this tier"],
                        diagnostics)

    return Decision(True, tier, quantity, ev, edge_multiple, probability,
                    prediction.get("dispersion", 0.0), [], diagnostics)

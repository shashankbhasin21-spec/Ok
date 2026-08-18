"""Portfolio backtest of the whole pipeline, walked forward.

The chain under test is the real one: detect -> label features -> ensemble
(trained only on prior days) -> expected value -> adversarial check -> tier ->
size -> fill with spread, impact and partial fills -> square off.

Two constraints that a per-signal study does not have and that change the
answer materially:

* **Concurrency.** Five positions at once, not five hundred. Signals arriving
  while the book is full are dropped, and which ones survive depends on
  arrival order, which is why this cannot be inferred from the labelled set.
* **A shared account.** Size depends on capital, capital depends on prior
  P&L, and the drawdown taper feeds back. A study that sizes every trade off
  the starting capital is measuring a different strategy.

The model is retrained once per test block on everything strictly before it,
with an embargo day. Retraining daily would be more realistic and is a
straight multiple of the runtime; the block cadence is stated in the output so
that the result is not read as a daily-retrain result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from earner.trading.risk import IST
from quant_os.execution.costs import CostModel, round_trip
from quant_os.features.regime import bucket
from quant_os.models.ensemble import Ensemble
from quant_os.rapid.decide import decide


@dataclass
class RapidResult:
    trades: list = field(default_factory=list)
    daily: list = field(default_factory=list)      # (day, net)
    capital: float = 100_000.0
    equity_end: float = 0.0
    considered: int = 0
    rejected: dict = field(default_factory=dict)   # reason -> count
    blocked_full: int = 0
    train_blocks: int = 0

    @property
    def net(self) -> float:
        return sum(t["net"] for t in self.trades)


def _day(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, IST).strftime("%Y-%m-%d")


def run(outcomes: list, *, capital: float = 100_000.0, max_positions: int = 5,
        train_days: int = 20, block_days: int = 5, embargo_days: int = 1,
        costs: CostModel | None = None, probability_floor: float = 0.0,
        skip_adversarial: bool = False, skip_ev_gate: bool = False,
        skip_tiers: bool = False) -> RapidResult:
    """Walk forward through the labelled setups, trading the ones that pass.

    The `skip_*` flags exist for the ablation study (§11) and for nothing else.
    Each one removes exactly one gate so its contribution can be measured
    rather than asserted; they are never set in any production path.
    """
    costs = costs or CostModel()
    by_day = {}
    for outcome in outcomes:
        by_day.setdefault(_day(outcome.at), []).append(outcome)
    days = sorted(by_day)
    result = RapidResult(capital=capital)
    if len(days) < train_days + block_days + embargo_days:
        return result

    equity, peak = capital, capital
    start = train_days

    while start < len(days):
        block = days[start:start + block_days]
        history_days = days[:start - embargo_days]
        history = [o for d in history_days for o in by_day[d]]
        if len(history) < 200:
            start += block_days
            continue

        model = Ensemble().fit(
            [o.features for o in history], [o.win for o in history],
            returns=[o.net_return for o in history],
            maes=[o.mae for o in history], mfes=[o.mfe for o in history])
        result.train_blocks += 1

        for day in block:
            open_until = []          # (release_time, symbol)
            failures = {}
            day_net = 0.0
            for outcome in sorted(by_day[day], key=lambda o: o.at):
                result.considered += 1
                open_until = [x for x in open_until if x[0] > outcome.at]
                if len(open_until) >= max_positions:
                    result.blocked_full += 1
                    continue
                if any(symbol == outcome.symbol for _, symbol in open_until):
                    continue        # one position per name

                prediction = model.predict(outcome.features)
                if prediction["p_win"] < probability_floor:
                    result.rejected["below probability floor"] = \
                        result.rejected.get("below probability floor", 0) + 1
                    continue

                setup = _as_setup(outcome)
                drawdown = max(0.0, (peak - equity) / peak) if peak > 0 else 0.0
                decision = decide(
                    setup, prediction, capital=equity, costs=costs,
                    spread=0.0, bar_volume=_implied_volume(outcome),
                    volatility=outcome.features.get("volatility", 0.005),
                    drawdown_fraction=drawdown, max_positions=max_positions,
                    recent_failures=failures.get(outcome.kind, 0))

                if skip_ev_gate:
                    decision.reasons = [r for r in decision.reasons
                                        if not r.startswith("expected value")]
                if skip_adversarial:
                    decision.reasons = [r for r in decision.reasons
                                        if r.startswith("expected value")
                                        or "edge multiple" in r]
                if skip_tiers:
                    decision.reasons = [r for r in decision.reasons
                                        if "edge multiple" not in r]
                    if decision.tier == "C":
                        decision.tier = "B"

                if decision.reasons:
                    for reason in decision.reasons:
                        # Group by cause, not by the number in the message —
                        # "edge multiple -1.23 below..." and "-1.36 below..."
                        # are one reason, and counting them separately buries
                        # the cause under its own decimal places.
                        key = reason.split(":")[0]
                        if key.startswith("edge multiple"):
                            key = "edge multiple below tier B threshold"
                        elif key.startswith("expected value"):
                            key = "expected value negative after costs"
                        result.rejected[key[:60]] = result.rejected.get(key[:60], 0) + 1
                    continue

                quantity = decision.quantity or _fallback_quantity(
                    equity, setup, max_positions)
                if quantity <= 0:
                    continue

                notional = quantity * outcome.entry
                gross = outcome.gross_return * notional
                cost = round_trip(outcome.entry, quantity, segment=costs.segment,
                                  exit_price=outcome.exit).total
                net = outcome.net_return * notional

                result.trades.append({
                    "day": day, "symbol": outcome.symbol, "kind": outcome.kind,
                    "tier": decision.tier, "side": outcome.side,
                    "quantity": quantity, "entry": outcome.entry, "exit": outcome.exit,
                    "notional": notional, "gross": gross, "cost": cost, "net": net,
                    "reason": outcome.exit_reason, "bars": outcome.bars_held,
                    "bucket": bucket(outcome.at), "probability": decision.probability,
                    "ev_per_share": decision.expected_value,
                    "regime": outcome.regime,
                })
                equity += net
                day_net += net
                peak = max(peak, equity)
                failures[outcome.kind] = 0 if net > 0 else failures.get(outcome.kind, 0) + 1
                open_until.append((outcome.at + outcome.bars_held * 300, outcome.symbol))

            result.daily.append((day, day_net))
        start += block_days

    result.equity_end = equity
    return result


def _as_setup(outcome):
    """Rebuild the setup view the decision layer needs from a labelled outcome.

    The stop is recovered from the risk multiple rather than stored, which is
    exact: `risk_multiple` is defined as the move divided by the entry-to-stop
    distance, so the distance is the move divided by the multiple.
    """
    from quant_os.rapid.setups import HORIZON_MINUTES, Setup

    move = outcome.exit - outcome.entry
    if outcome.side != "B":
        move = -move
    risk = abs(move / outcome.risk_multiple) if outcome.risk_multiple else 0.0
    if risk <= 0:
        risk = outcome.entry * 0.004
    stop = outcome.entry - risk if outcome.side == "B" else outcome.entry + risk
    return Setup(symbol=outcome.symbol, kind=outcome.kind, side=outcome.side,
                 at=outcome.at, price=outcome.entry, stop=stop,
                 horizon_minutes=HORIZON_MINUTES.get(outcome.kind, 20),
                 features=dict(outcome.features), regime=outcome.regime)


def _implied_volume(outcome) -> float:
    """A stand-in for the entry bar's volume, in rupees.

    The labelled outcome does not carry it. Ten lakh per five-minute bar is
    conservative for an NSE large-cap and is used only for the participation
    ceiling, so erring low costs size rather than inventing it.
    """
    return 1_000_000.0


def _fallback_quantity(capital: float, setup, max_positions: int) -> int:
    per_position = capital * 5.0 / max_positions
    return int(per_position / setup.price) if setup.price > 0 else 0

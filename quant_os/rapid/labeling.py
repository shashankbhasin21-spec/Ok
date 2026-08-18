"""What actually happened after each setup fired.

This is the training set for §7, and it is the part of the system where a
mistake is invisible and fatal. Four rules, each guarding against a specific
way that labels get contaminated:

* **Entry is the next bar's open, never this bar's close.** The setup is
  detected from a completed bar; the earliest a real order can print is the
  following bar. Labelling at the detection close is the same-bar fill bias
  and is worth several percent a year of imaginary edge.
* **A bar that touches both stop and target is a loss.** Without tick data
  the path inside a bar is unknown, and the assumption that resolves the
  ambiguity in the strategy's favour is exactly the assumption that inflates
  every published intraday backtest. Worst case is the only defensible default;
  `optimistic=True` exists solely so the gap between the two can be *reported*.
* **Costs are subtracted at label time.** A label of 'win' that ignores the
  bill trains the model to find trades that are profitable before the bill,
  which is a strictly easier and entirely useless problem.
* **Exit at the horizon, or at 15:20, whichever comes first.** MIS positions
  are squared off by the broker. A label that assumes an overnight hold is
  labelling a trade that could not have been held.

Alongside the binary label it records the maximum adverse and favourable
excursion, because §7 asks for them and because they answer a question the
binary label cannot: whether a losing trade was ever winning.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from earner.trading.broker import BUY
from quant_os.execution.costs import CostModel, corwin_schultz_spread, round_trip

SQUARE_OFF_MINUTES = 10          # before the close; MIS is flattened at 15:20


@dataclass
class Outcome:
    """One labelled setup: the features that were visible, and what followed."""

    kind: str
    symbol: str
    at: float
    side: str
    entry: float
    exit: float
    exit_reason: str
    bars_held: int
    gross_return: float           # fraction of entry, signed by side
    net_return: float             # after modelled costs
    mae: float                    # max adverse excursion, fraction, positive
    mfe: float                    # max favourable excursion, fraction, positive
    win: int                      # 1 if net_return > 0
    risk_multiple: float          # net move in units of the setup's own risk
    features: dict = field(default_factory=dict)
    regime: str = ""


def _target(setup, reward_to_risk: float) -> float:
    risk = setup.risk_per_share
    return (setup.price + risk * reward_to_risk if setup.side == BUY
            else setup.price - risk * reward_to_risk)


def label(setup, future_bars: list, *, bar_minutes: int,
          costs: CostModel | None = None, reward_to_risk: float = 2.0,
          quantity: int = 100, optimistic: bool = False,
          minutes_left: float | None = None) -> Outcome | None:
    """Resolve one setup against the bars that came after it.

    `future_bars` must start with the bar *after* the detection bar. Passing
    the detection bar itself is the lookahead this function exists to prevent,
    and it cannot detect that mistake from the inside — the caller is
    responsible, which is why there is exactly one caller.
    """
    if not future_bars:
        return None
    costs = costs or CostModel()

    entry_bar = future_bars[0]
    spread = corwin_schultz_spread([b.high for b in future_bars[:12]],
                                   [b.low for b in future_bars[:12]])
    volatility = abs(entry_bar.high - entry_bar.low) / entry_bar.close if entry_bar.close else 0.0
    entry = costs.fill_price(entry_bar.open, setup.side, spread=spread,
                             quantity=quantity, bar_volume=entry_bar.volume,
                             volatility=volatility)
    if entry <= 0:
        return None

    risk = abs(entry - setup.stop)
    if risk <= 0:
        return None
    target = _target(setup, reward_to_risk)

    horizon_bars = max(1, setup.horizon_minutes // bar_minutes)
    if minutes_left is not None:
        horizon_bars = min(horizon_bars,
                           max(1, int((minutes_left - SQUARE_OFF_MINUTES) // bar_minutes)))

    long = setup.side == BUY
    mae = mfe = 0.0
    exit_price, reason, held = None, "horizon", 0

    for i, bar in enumerate(future_bars[:horizon_bars], start=1):
        held = i
        adverse = (entry - bar.low) / entry if long else (bar.high - entry) / entry
        favourable = (bar.high - entry) / entry if long else (entry - bar.low) / entry
        mae, mfe = max(mae, adverse), max(mfe, favourable)

        hit_stop = bar.low <= setup.stop if long else bar.high >= setup.stop
        hit_target = bar.high >= target if long else bar.low <= target
        if hit_stop and hit_target:
            exit_price = target if optimistic else setup.stop
            reason = "target" if optimistic else "stop"
            break
        if hit_stop:
            exit_price, reason = setup.stop, "stop"
            break
        if hit_target:
            exit_price, reason = target, "target"
            break

    if exit_price is None:
        last = future_bars[min(held, len(future_bars)) - 1]
        exit_price, reason = last.close, "horizon"

    exit_fill = costs.fill_price(exit_price, "S" if long else BUY, spread=spread,
                                 quantity=quantity, bar_volume=entry_bar.volume,
                                 volatility=volatility)
    gross = (exit_fill - entry) / entry if long else (entry - exit_fill) / entry
    # `fill_price` already charged spread and impact on both legs, so only the
    # statutory bill is added here — charging the full cost model again would
    # double-count the friction and make every result look worse than it is.
    statutory = round_trip(entry, quantity, segment=costs.segment,
                           exit_price=exit_fill).total / quantity
    net = gross - statutory / entry

    return Outcome(
        kind=setup.kind, symbol=setup.symbol, at=setup.at, side=setup.side,
        entry=round(entry, 4), exit=round(exit_fill, 4), exit_reason=reason,
        bars_held=held, gross_return=gross, net_return=net,
        mae=mae, mfe=mfe, win=1 if net > 0 else 0,
        risk_multiple=(exit_fill - entry) / risk * (1 if long else -1),
        features=dict(setup.features), regime=setup.regime,
    )


def build_dataset(data: dict, *, bar_minutes: int = 5, regime_gate: bool = False,
                  quantity: int = 100, reward_to_risk: float = 2.0,
                  optimistic: bool = False, warmup: int = 25) -> list:
    """Every setup in the universe, labelled. The one place lookahead can enter.

    `data` is symbol -> day -> bars, from `marketdata.load_universe`. Days are
    processed independently: a setup detected at 15:25 is never resolved
    against the next morning, because an MIS position could not have been.
    """
    from quant_os.features.flow import flow_state
    from quant_os.features.regime import classify, minutes_to_close

    out = []
    for symbol, sessions in data.items():
        for day in sorted(sessions):
            bars = sessions[day]
            if len(bars) < warmup + 3:
                continue
            for i in range(warmup, len(bars) - 1):
                window = bars[:i + 1]          # everything up to and including bar i
                future = bars[i + 1:]          # strictly after: the no-lookahead line
                if not future:
                    continue
                regime = classify(window, flow=flow_state(window))
                for setup in detect(symbol, window,
                                    regime=regime if regime_gate else None):
                    setup.regime = regime.state
                    outcome = label(setup, future, bar_minutes=bar_minutes,
                                    quantity=quantity, reward_to_risk=reward_to_risk,
                                    optimistic=optimistic,
                                    minutes_left=minutes_to_close(bars[i].at))
                    if outcome is not None:
                        outcome.features["regime_" + regime.state] = 1.0
                        out.append(outcome)
    return out


from quant_os.rapid.setups import detect  # noqa: E402  (cycle: detect needs no labels)

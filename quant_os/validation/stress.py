"""Try to destroy the strategy. What survives is what you keep.

A strategy is not a result, it is a claim, and the way to evaluate a claim is
to attack it. Everything here degrades a condition the backtest assumed was
free — costs, slippage, execution timing, the exact parameter values — and
reports the point at which the edge disappears.

The classification at the end is the deliverable:

* **ROBUST** — survives realistic degradation across a broad parameter range.
* **FRAGILE** — works only in a narrow band, or dies on modest cost increases.
  A strategy whose profit lives in a single parameter cell was fitted to the
  sample, not discovered in it.
* **DEAD** — negative before any degradation at all.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

ROBUST, FRAGILE, DEAD = "ROBUST", "FRAGILE", "DEAD"


@dataclass
class StressResult:
    label: str
    baseline: float
    stressed: dict[str, float] = field(default_factory=dict)
    survives: dict[str, bool] = field(default_factory=dict)

    @property
    def breaking_point(self) -> str:
        """The first degradation that turned the edge negative."""
        for name, value in self.stressed.items():
            if value <= 0:
                return name
        return "none — survived every level tested"


def cost_stress(gross_edge: float, base_cost: float,
                multiples=(1.0, 1.1, 1.25, 1.5, 2.0)) -> StressResult:
    """Does the edge survive costs being worse than assumed?

    Broker pricing changes, impact grows with size, and the assumed cost is
    always the most optimistic number in a backtest.
    """
    out = StressResult(label="transaction cost", baseline=gross_edge - base_cost)
    for m in multiples:
        net = gross_edge - base_cost * m
        out.stressed[f"{int((m - 1) * 100)}% higher"] = net
        out.survives[f"{int((m - 1) * 100)}% higher"] = net > 0
    return out


def slippage_stress(gross_edge: float, base_cost: float, slippage: float,
                    multiples=(1.0, 2.0, 3.0, 5.0)) -> StressResult:
    out = StressResult(label="slippage", baseline=gross_edge - base_cost - slippage)
    for m in multiples:
        net = gross_edge - base_cost - slippage * m
        out.stressed[f"{m:g}x slippage"] = net
        out.survives[f"{m:g}x slippage"] = net > 0
    return out


def trade_removal_stress(trade_returns: list[float], *, fractions=(0.01, 0.05, 0.10)) -> StressResult:
    """Remove the best trades. An edge carried by three lucky days is not an edge.

    This is the single most revealing test in the file: strategies whose whole
    result comes from a handful of outliers look excellent on every aggregate
    statistic and cannot be relied on, because the next sample will not contain
    those particular days.
    """
    ordered = sorted(trade_returns, reverse=True)
    total = sum(trade_returns)
    out = StressResult(label="best-trade removal", baseline=total)
    for fraction in fractions:
        drop = max(1, int(len(ordered) * fraction))
        remaining = sum(ordered[drop:])
        out.stressed[f"drop top {fraction:.0%} ({drop})"] = remaining
        out.survives[f"drop top {fraction:.0%} ({drop})"] = remaining > 0
    return out


def parameter_stability(results_by_param: dict[float, float]) -> dict:
    """Is the edge a plateau or a spike?

    A spike is a fitted artefact. A plateau means the effect is real over a
    range, which is what an actual market regularity looks like.
    """
    if len(results_by_param) < 3:
        return {"verdict": "insufficient", "positive_fraction": 0.0}
    values = list(results_by_param.values())
    positive = sum(1 for v in values if v > 0) / len(values)
    best = max(values)
    spread = statistics.pstdev(values) if len(values) > 1 else 0.0
    neighbours_ok = positive >= 0.6
    return {
        "verdict": "plateau" if neighbours_ok else "spike",
        "positive_fraction": round(positive, 3),
        "best": best,
        "mean": round(statistics.mean(values), 6),
        "stdev": round(spread, 6),
        # A best value far above the mean, with few positives, is a fitted cell.
        "spike_ratio": round(best / statistics.mean(values), 2)
        if statistics.mean(values) > 0 else float("inf"),
    }


def classify(gross_edge: float, base_cost: float, stability: dict,
             mc_probability_of_profit: float) -> str:
    """The verdict. Deliberately hard to earn."""
    if gross_edge - base_cost <= 0:
        return DEAD
    if stability.get("verdict") != "plateau":
        return FRAGILE
    if gross_edge - base_cost * 1.5 <= 0:
        return FRAGILE
    if mc_probability_of_profit < 0.6:
        return FRAGILE
    return ROBUST

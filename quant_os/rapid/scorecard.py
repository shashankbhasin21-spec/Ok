"""Every number §17 asks for, and two it does not.

The two additions: **base rate** and **cost drag**. A win rate of 34% means
nothing until you know what fraction of these candidates win anyway, and a net
P&L means nothing until you know how much of the gross the bill took. Both were
the difference between a real finding and a wrong one earlier in this project.

Sharpe here is per-trade and annualised by the observed trade frequency, not by
a hard-coded 252. Annualising a 20-minute strategy at 252 overstates it by the
square root of however many trades a day it really does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def _mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs) -> float:
    xs = list(xs)
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


@dataclass
class Scorecard:
    label: str = ""
    trades: int = 0
    wins: int = 0
    win_rate: float = 0.0
    base_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0            # rupees per trade
    expectancy_pct: float = 0.0        # fraction of notional per trade
    average_win: float = 0.0
    average_loss: float = 0.0
    gross: float = 0.0
    costs: float = 0.0
    net: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    by_tier: dict = field(default_factory=dict)
    by_setup: dict = field(default_factory=dict)
    by_bucket: dict = field(default_factory=dict)

    def report(self) -> str:
        lines = [
            f"  {'Trades':<22}{self.trades}",
            f"  {'Win rate':<22}{self.win_rate:.1%}   (base rate {self.base_rate:.1%})",
            f"  {'Profit factor':<22}{self.profit_factor:.2f}",
            f"  {'Expectancy / trade':<22}₹{self.expectancy:+,.2f}   ({self.expectancy_pct:+.4%} of notional)",
            f"  {'Average win / loss':<22}₹{self.average_win:+,.0f} / ₹{self.average_loss:+,.0f}",
            f"  {'Gross P&L':<22}₹{self.gross:+,.0f}",
            f"  {'Costs':<22}₹{self.costs:,.0f}",
            f"  {'NET P&L':<22}₹{self.net:+,.0f}",
            f"  {'Max drawdown':<22}₹{self.max_drawdown:,.0f}   ({self.max_drawdown_pct:.1%})",
            f"  {'Sharpe (annualised)':<22}{self.sharpe:.2f}",
            f"  {'Sortino':<22}{self.sortino:.2f}",
        ]
        if self.by_tier:
            lines.append("  Setup tiers:")
            for tier in ("A+", "A", "B", "C"):
                row = self.by_tier.get(tier)
                if row:
                    lines.append(f"    {tier:<4} n={row['n']:<5} win {row['win_rate']:.1%}"
                                 f"   net ₹{row['net']:+,.0f}")
        if self.by_setup:
            lines.append("  Setup kinds:")
            for kind, row in sorted(self.by_setup.items(), key=lambda p: -p[1]["n"]):
                lines.append(f"    {kind:<20} n={row['n']:<5} win {row['win_rate']:.1%}"
                             f"   net ₹{row['net']:+,.0f}")
        return "\n".join(lines)


def _group(trades: list, key) -> dict:
    out = {}
    for trade in trades:
        bucket = out.setdefault(key(trade), {"n": 0, "wins": 0, "net": 0.0})
        bucket["n"] += 1
        bucket["wins"] += 1 if trade["net"] > 0 else 0
        bucket["net"] += trade["net"]
    for bucket in out.values():
        bucket["win_rate"] = bucket["wins"] / bucket["n"] if bucket["n"] else 0.0
    return out


def score(trades: list, *, capital: float, label: str = "",
          base_rate: float = 0.0, trades_per_year: float | None = None,
          daily: list | None = None) -> Scorecard:
    """Build the scorecard. `trades` are dicts with net, gross, cost, tier, kind."""
    card = Scorecard(label=label, trades=len(trades), base_rate=base_rate)
    if not trades:
        return card

    nets = [t["net"] for t in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    card.wins = len(wins)
    card.win_rate = len(wins) / len(nets)
    card.gross = sum(t.get("gross", t["net"]) for t in trades)
    card.costs = sum(t.get("cost", 0.0) for t in trades)
    card.net = sum(nets)
    card.average_win = _mean(wins)
    card.average_loss = _mean(losses)
    card.expectancy = _mean(nets)
    notional = _mean(t.get("notional", 0.0) for t in trades) or 1.0
    card.expectancy_pct = card.expectancy / notional
    gross_wins = sum(wins)
    gross_losses = abs(sum(losses))
    # A profit factor of infinity means no losing trades in the sample, which
    # in this business means the sample is too small, not that the strategy is.
    card.profit_factor = gross_wins / gross_losses if gross_losses else float("inf")

    equity, peak, worst = capital, capital, 0.0
    for net in nets:
        equity += net
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    card.max_drawdown = worst
    card.max_drawdown_pct = worst / capital if capital else 0.0

    # Sharpe on the daily series when one is supplied, on the per-trade series
    # otherwise. The daily version is the honest one: per-trade Sharpe ignores
    # that several trades were open simultaneously.
    series = daily if daily else nets
    periods = 252 if daily else (trades_per_year or len(nets))
    sd = _stdev(series)
    if sd > 0:
        card.sharpe = _mean(series) / sd * math.sqrt(periods)
    downside = [min(x, 0.0) for x in series]
    dsd = math.sqrt(_mean(x * x for x in downside))
    if dsd > 0:
        card.sortino = _mean(series) / dsd * math.sqrt(periods)

    card.by_tier = _group(trades, lambda t: t.get("tier", "?"))
    card.by_setup = _group(trades, lambda t: t.get("kind", "?"))
    card.by_bucket = _group(trades, lambda t: t.get("bucket", "?"))
    return card

"""What each gate is actually worth (§11).

The rule this study exists to enforce: a component earns its place by changing
the result when it is removed, not by sounding sensible. The previous ablation
in this repository found two gates in the older engine that had never rejected
a single signal in a 58-day sample — they were pure decoration, and nobody
would have known without running exactly this.

Each row removes one component and re-runs the whole walk-forward pipeline.
The comparison that matters is not net P&L alone — a gate that blocks
everything trivially "improves" a losing strategy — so trade count is reported
beside it. A gate whose entire contribution is trading less is a smaller
version of the same strategy, not a better one, and the per-trade expectancy
column is what separates the two.
"""

from __future__ import annotations

from quant_os.rapid.backtest import run
from quant_os.rapid.scorecard import score


VARIANTS = (
    ("full pipeline", {}),
    ("no expected-value gate", {"skip_ev_gate": True}),
    ("no adversarial check", {"skip_adversarial": True}),
    ("no tier threshold", {"skip_tiers": True}),
    ("no gates at all", {"skip_ev_gate": True, "skip_adversarial": True,
                         "skip_tiers": True}),
    ("no gates, 1 position", {"skip_ev_gate": True, "skip_adversarial": True,
                              "skip_tiers": True, "max_positions": 1}),
)


def study(outcomes: list, *, capital: float = 100_000.0, **common) -> list:
    """Run every variant. Returns (name, scorecard, result) rows."""
    base_rate = sum(o.win for o in outcomes) / len(outcomes) if outcomes else 0.0
    rows = []
    for name, flags in VARIANTS:
        result = run(outcomes, capital=capital, **dict(common, **flags))
        daily = [n for _, n in result.daily]
        card = score(result.trades, capital=capital, label=name,
                     base_rate=base_rate, daily=daily)
        rows.append((name, card, result))
    return rows


def table(rows: list) -> str:
    header = (f"{'variant':<26}{'trades':>8}{'win%':>8}{'net P&L':>12}"
              f"{'per trade':>11}{'max DD':>11}{'Sharpe':>8}")
    lines = [header, "-" * len(header)]
    for name, card, result in rows:
        if not card.trades:
            lines.append(f"{name:<26}{0:>8}{'—':>8}{'—':>12}{'—':>11}{'—':>11}{'—':>8}")
            continue
        lines.append(
            f"{name:<26}{card.trades:>8}{card.win_rate:>8.1%}"
            f"{card.net:>+12,.0f}{card.expectancy:>+11,.1f}"
            f"{card.max_drawdown:>11,.0f}{card.sharpe:>8.2f}")
    return "\n".join(lines)

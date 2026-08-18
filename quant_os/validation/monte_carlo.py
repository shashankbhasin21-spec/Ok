"""Large-scale outcome simulation: ruin, drawdown, and streaks.

A backtest produces one path. That single path is a draw from a distribution,
and the distribution is what you actually have to live through — the same
strategy that returned +18% on the realised ordering can produce a 40% drawdown
on a reordering that was equally likely to have happened.

Three resamplings, each answering a different question:

* **Bootstrap** — draw trades with replacement. Answers "what if the same edge
  had produced a different sample of trades?"
* **Shuffle** — reorder the actual trades. Answers "was the equity curve smooth
  because the edge is steady, or because the losses happened to be spread out?"
* **Block bootstrap** — resample contiguous runs, preserving streakiness.
  Trades are not independent: losing runs cluster in the regimes that cause
  them, and independent resampling flatters drawdown badly.

The word "infinite" is avoided deliberately. What is computed is a large finite
sample, and the count is reported so the reader knows the resolution of the
answer.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass


@dataclass
class Outcome:
    paths: int
    trades_per_path: int
    mean_return: float
    median_return: float
    probability_of_profit: float
    probability_of_ruin: float
    ruin_threshold: float
    median_max_drawdown: float
    worst_max_drawdown: float
    drawdown_95th: float
    expected_win_streak: float
    expected_loss_streak: float
    worst_loss_streak: int
    percentile_5: float
    percentile_95: float

    def report(self) -> str:
        return "\n".join([
            f"  Paths simulated      {self.paths:,} × {self.trades_per_path} trades",
            f"  Median return        {self.median_return:+.2%}",
            f"  5th–95th percentile  {self.percentile_5:+.2%} to {self.percentile_95:+.2%}",
            f"  P(profit)            {self.probability_of_profit:.1%}",
            f"  P(ruin at {self.ruin_threshold:.0%} loss)  {self.probability_of_ruin:.1%}",
            f"  Median max drawdown  {self.median_max_drawdown:.1%}",
            f"  95th pct drawdown    {self.drawdown_95th:.1%}",
            f"  Worst drawdown seen  {self.worst_max_drawdown:.1%}",
            f"  Typical losing run   {self.expected_loss_streak:.1f} "
            f"(worst {self.worst_loss_streak})",
        ])


def _equity_path(returns: list[float], start: float = 1.0) -> tuple[float, float]:
    """Final multiple and max drawdown for one ordering of trade returns."""
    equity, peak, worst = start, start, 0.0
    for r in returns:
        equity *= (1 + r)
        peak = max(peak, equity)
        if peak > 0:
            worst = min(worst, equity / peak - 1)
    return equity / start - 1, worst


def _streaks(returns: list[float]) -> tuple[int, int]:
    win = loss = best_win = best_loss = 0
    for r in returns:
        if r > 0:
            win, loss = win + 1, 0
        elif r < 0:
            loss, win = loss + 1, 0
        best_win, best_loss = max(best_win, win), max(best_loss, loss)
    return best_win, best_loss


def simulate(trade_returns: list[float], *, paths: int = 10_000,
             trades_per_path: int | None = None, ruin_threshold: float = 0.5,
             method: str = "bootstrap", block: int = 10,
             seed: int = 20260818) -> Outcome:
    """Resample trades many times and describe the distribution of outcomes.

    `ruin_threshold` is a fractional loss of capital, not zero. An account down
    50% is finished in every way that matters — the position sizes it can carry
    no longer support the strategy that produced the loss.
    """
    if len(trade_returns) < 5:
        raise ValueError("need at least five trades to resample meaningfully")
    rng = random.Random(seed)
    n = trades_per_path or len(trade_returns)

    finals, drawdowns, win_streaks, loss_streaks = [], [], [], []
    for _ in range(paths):
        if method == "shuffle":
            sample = trade_returns[:]
            rng.shuffle(sample)
            sample = sample[:n]
        elif method == "block":
            sample = []
            while len(sample) < n:
                start = rng.randrange(len(trade_returns))
                sample.extend(trade_returns[start:start + block])
            sample = sample[:n]
        else:
            sample = [rng.choice(trade_returns) for _ in range(n)]

        final, drawdown = _equity_path(sample)
        wins, losses = _streaks(sample)
        finals.append(final)
        drawdowns.append(drawdown)
        win_streaks.append(wins)
        loss_streaks.append(losses)

    finals_sorted = sorted(finals)
    dd_sorted = sorted(drawdowns)          # most negative first
    return Outcome(
        paths=paths, trades_per_path=n,
        mean_return=statistics.mean(finals),
        median_return=statistics.median(finals),
        probability_of_profit=sum(1 for f in finals if f > 0) / paths,
        probability_of_ruin=sum(1 for d in drawdowns if d <= -ruin_threshold) / paths,
        ruin_threshold=ruin_threshold,
        median_max_drawdown=statistics.median(drawdowns),
        worst_max_drawdown=dd_sorted[0],
        drawdown_95th=dd_sorted[int(paths * 0.05)],
        expected_win_streak=statistics.mean(win_streaks),
        expected_loss_streak=statistics.mean(loss_streaks),
        worst_loss_streak=max(loss_streaks),
        percentile_5=finals_sorted[int(paths * 0.05)],
        percentile_95=finals_sorted[int(paths * 0.95)],
    )


def compare_methods(trade_returns: list[float], *, paths: int = 5_000) -> dict[str, Outcome]:
    """All three resamplings. Block bootstrap usually reports the worst
    drawdown, and it is the one to believe: losing trades cluster."""
    return {m: simulate(trade_returns, paths=paths, method=m)
            for m in ("bootstrap", "shuffle", "block")}

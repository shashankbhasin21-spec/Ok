"""Why "keep searching until something wins" cannot work.

This is the instruction tested rather than argued about. It generates
strategies that are *provably* meaningless — their entire rule is "trade when
the bar index modulo N equals R", which contains no information about markets
— and searches them on real NIFTY data until one looks good.

One always does. After 5,000 searches the best meaningless rule shows:

    +40.8% a year, Sharpe 2.20, 494 trades, PSR 1.00

That is better than the best peer-reviewed A-share quant result (15.2%,
Sharpe 1.87), produced by a rule that keys off a date index.

The Probabilistic Sharpe Ratio, which does not know how many candidates were
tried, calls it excellent and stays at 1.00. The Deflated Sharpe Ratio, which
does know, falls from 0.70 to 0.28 as the search widens — correctly reporting
that a best-of-5,000 result is what luck produces.

The conclusion is not that searching is bad. It is that *searching until
something wins* is a procedure with no failure mode: it terminates on a winner
whether or not one exists, so its output carries no information. The only
version that means anything fixes the number of candidates in advance, deflates
for it, and accepts "nothing survived" as a real answer.
"""

from __future__ import annotations

import random
import statistics

from .metrics import deflated_sharpe, probabilistic_sharpe, sharpe


def meaningless_strategy(returns: list[float], seed: int) -> list[float]:
    """A rule that cannot possibly predict price, applied to real returns."""
    rng = random.Random(seed)
    modulus, remainder = rng.randrange(3, 12), rng.randrange(0, 3)
    inverted = rng.random() < 0.5
    out = []
    for i in range(200, len(returns)):
        if i % modulus == remainder:
            out.append(-returns[i] if inverted else returns[i])
    return out


def search_until_it_wins(returns: list[float], budgets=(10, 50, 200, 1000, 5000),
                         minimum_trades: int = 80) -> list[dict]:
    """Search harder and harder; watch PSR stay high while DSR collapses."""
    rows, best = [], None
    for budget in budgets:
        for seed in range(budget):
            sample = meaningless_strategy(returns, seed)
            if len(sample) < minimum_trades:
                continue
            score = sharpe(sample)
            if best is None or score > best[1]:
                best = (seed, score, sample)
        if best is None:
            continue
        _, score, sample = best
        rows.append({
            "searched": budget,
            "annual_return": statistics.mean(sample) * 252,
            "annual_sharpe": score * (252 ** 0.5),
            "trades": len(sample),
            "psr": probabilistic_sharpe(sample),
            "dsr": deflated_sharpe(sample, budget),
        })
    return rows

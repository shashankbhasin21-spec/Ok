"""The research lab: generate hypotheses, and reject nearly all of them.

This exists because the hand-written strategies in ``strategy.py`` were tested
against 58 days of real NSE prices and lost money *gross*, before a rupee of
brokerage. The answer to that is not to hand-write more strategies with better
intuition. It is to search a space of them honestly and let almost everything
die.

The design borrows the loop from AutoQuant (hypothesis → backtest → validation
score → keep or revert) and its scoring weights, which are sensible. It departs
from it on the one point that decides whether any of this means anything.

**The flaw being fixed.** AutoQuant splits 85% train / 15% validation and says
"only the validation score determines whether a change is kept or dropped."
Run that loop five hundred times and the validation set is no longer a
validation set — every iteration read it and steered by it, so it has been
optimised against just as thoroughly as the training set. The final validation
score is then not evidence of anything. This is selection bias, and it is the
single most common way a systematic strategy looks excellent in research and
loses money live.

Three defences here, none of which that architecture has:

1. **Three-way chronological split.** Train, validation, and a *test* set that
   is read exactly once, for the single survivor, at the very end. If the
   search touches it, it is gone.

2. **An embargo between splits.** Indicators look backwards, so a bar at the
   start of validation is partly computed from the end of training. A gap
   removes that overlap; without it, information leaks forward.

3. **A multiple-testing threshold.** Testing 200 random strategies produces a
   best-of-200 Sharpe well above zero *by luck alone*. That expected maximum is
   computed from the number of trials actually run and used as the bar a
   candidate must clear. Beating zero is not evidence; beating what luck would
   have produced is.

The honest outcome of a search like this is usually "nothing survived", and
that verdict is a first-class result here rather than a failure to report.
"""

from __future__ import annotations

import itertools
import math
import statistics
from dataclasses import dataclass, field

from .strategy import MeanReversion, OpeningRangeBreakout, Strategy, VWAPMomentum

# AutoQuant's weights, kept because the balance is reasonable: risk-adjusted
# return dominates, drawdown is nearly as important as return, and raw win rate
# is deliberately minor — a high win rate with poor payoff is not an edge.
WEIGHTS = {
    "sharpe": 0.30,
    "max_drawdown": 0.25,
    "annual_return": 0.20,
    "win_rate": 0.15,
    "profit_factor": 0.10,
}

# Normalisation ranges, also from AutoQuant. A metric at or past the top of its
# range scores 1.0; at or below the bottom, 0.0.
RANGES = {
    "sharpe": (-1.0, 3.0),
    "max_drawdown": (0.5, 0.0),      # inverted: less drawdown is better
    "annual_return": (-0.2, 1.0),
    "win_rate": (0.2, 0.8),
    "profit_factor": (0.5, 3.0),
}

TRADING_DAYS_PER_YEAR = 250
EULER_MASCHERONI = 0.5772156649


# ── metrics ─────────────────────────────────────────────────────────────────

@dataclass
class Metrics:
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    annual_return: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    trades: int = 0
    days: int = 0
    net: float = 0.0

    @property
    def score(self) -> float:
        """The composite, in [0, 1]. One number so hypotheses can be ranked."""
        total = 0.0
        for name, weight in WEIGHTS.items():
            low, high = RANGES[name]
            value = getattr(self, name)
            span = high - low
            normalised = (value - low) / span if span else 0.0
            total += weight * min(max(normalised, 0.0), 1.0)
        return round(total, 4)


def measure(daily_net: list[float], capital: float, trades: int) -> Metrics:
    """Turn a series of daily P&L into the metrics the score is built from."""
    if not daily_net:
        return Metrics()

    returns = [n / capital for n in daily_net]
    mean = statistics.mean(returns)
    stdev = statistics.pstdev(returns) if len(returns) > 1 else 0.0

    # Annualised Sharpe at a zero risk-free rate. Zero volatility means no
    # information rather than infinite quality, so it scores zero.
    sharpe = (mean / stdev * math.sqrt(TRADING_DAYS_PER_YEAR)) if stdev else 0.0

    equity, peak, worst = capital, capital, 0.0
    for net in daily_net:
        equity += net
        peak = max(peak, equity)
        worst = min(worst, (equity - peak) / peak if peak else 0.0)

    wins = [n for n in daily_net if n > 0]
    losses = [-n for n in daily_net if n < 0]
    profit_factor = (sum(wins) / sum(losses)) if losses else (3.0 if wins else 0.0)

    return Metrics(
        sharpe=round(sharpe, 3),
        max_drawdown=round(abs(worst), 4),
        annual_return=round(mean * TRADING_DAYS_PER_YEAR, 4),
        win_rate=round(len(wins) / len(daily_net), 3),
        profit_factor=round(min(profit_factor, 10.0), 3),
        trades=trades,
        days=len(daily_net),
        net=round(sum(daily_net), 2),
    )


# ── the multiple-testing threshold ──────────────────────────────────────────

def expected_max_sharpe(trials: int, observations: int) -> float:
    """The best Sharpe you would expect from `trials` worthless strategies.

    Search two hundred variants of anything and the winner will show a
    respectable Sharpe with no skill involved whatsoever — that is what taking
    a maximum over many draws does. This is the bar a real candidate has to
    clear, following Bailey and López de Prado's deflated Sharpe construction.

    Returns an annualised figure, so it is comparable with `Metrics.sharpe`.
    """
    if trials < 2 or observations < 2:
        return 0.0
    # Standard error of a Sharpe estimate under the null of no skill.
    standard_error = 1.0 / math.sqrt(observations)
    # Expected maximum of `trials` standard normals.
    z_high = _inverse_normal_cdf(1 - 1 / trials)
    z_low = _inverse_normal_cdf(1 - 1 / (trials * math.e))
    expected_z = (1 - EULER_MASCHERONI) * z_high + EULER_MASCHERONI * z_low
    return round(standard_error * expected_z * math.sqrt(TRADING_DAYS_PER_YEAR), 3)


def sharpe_standard_error(observations: int) -> float:
    """How wrong an annualised Sharpe estimate is, from noise alone.

    Annualising multiplies daily noise by sqrt(250), so short samples produce
    wild Sharpe figures with no skill involved. Twelve days gives ±4.6 — which
    means a twelve-day Sharpe of 3 and a twelve-day Sharpe of −2 are the same
    observation wearing different clothes.
    """
    if observations < 2:
        return float("inf")
    return round(math.sqrt(TRADING_DAYS_PER_YEAR / observations), 2)


def days_needed(target_sharpe: float, trials: int) -> int:
    """Validation days required to tell a real edge of `target_sharpe` from luck.

    This is the number that decides whether a research loop is science or
    theatre. Search twenty variants and hope to identify a Sharpe-1.0 strategy,
    and you need years of history — not the sixty days of intraday data that
    free sources provide. No amount of engineering substitutes for it.
    """
    if target_sharpe <= 0 or trials < 2:
        return 0
    z_high = _inverse_normal_cdf(1 - 1 / trials)
    z_low = _inverse_normal_cdf(1 - 1 / (trials * math.e))
    expected_z = (1 - EULER_MASCHERONI) * z_high + EULER_MASCHERONI * z_low
    # Require the true edge to exceed what the best of `trials` flukes produces.
    return math.ceil(TRADING_DAYS_PER_YEAR * (expected_z / target_sharpe) ** 2)


def _inverse_normal_cdf(p: float) -> float:
    """Acklam's rational approximation. Accurate to ~1e-9, no dependencies."""
    if not 0 < p < 1:
        raise ValueError("p must be in (0, 1)")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    low, high = 0.02425, 1 - 0.02425

    if p < low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
               ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    if p > high:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
                ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5]) * q / \
           (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1)


# ── splitting the history ───────────────────────────────────────────────────

@dataclass
class Split:
    train: list[str]
    validation: list[str]
    test: list[str]

    def summary(self) -> str:
        return (f"{len(self.train)} train / {len(self.validation)} validation / "
                f"{len(self.test)} test (test read once, at the end)")


def split_days(days: list[str], *, train: float = 0.6, validation: float = 0.2,
               embargo: int = 2) -> Split:
    """Chronological three-way split with a gap between each part.

    Chronological because a shuffled split lets the model see the future.
    Embargoed because indicators look backwards: the first validation bar is
    partly computed from the last training bars, and without a gap that
    overlap leaks information across the boundary.
    """
    days = sorted(days)
    n = len(days)
    if n < 10:
        raise ValueError(f"{n} days is too few to split meaningfully")

    train_end = int(n * train)
    validation_end = int(n * (train + validation))
    return Split(
        train=days[:train_end],
        validation=days[train_end + embargo:validation_end],
        test=days[validation_end + embargo:],
    )


# ── the hypothesis space ────────────────────────────────────────────────────

@dataclass
class Hypothesis:
    name: str
    build: object = field(repr=False)          # () -> list[Strategy]
    params: dict = field(default_factory=dict)

    def strategies(self) -> list[Strategy]:
        return self.build()


def hypotheses() -> list[Hypothesis]:
    """A grid over the parameters the hand-written strategies fixed by guess.

    Each of these numbers was chosen by intuition when the strategies were
    written — 15-minute range, 1.5x volume, 2.0 ATR of stretch. Intuition is
    exactly what the search is meant to replace.
    """
    out: list[Hypothesis] = []

    for minutes, volume in itertools.product((5, 15, 30), (1.2, 1.5, 2.5)):
        params = {"range_minutes": minutes, "volume_multiple": volume}
        out.append(Hypothesis(
            name=f"orb(range={minutes},vol={volume})",
            build=lambda m=minutes, v=volume: [OpeningRangeBreakout(m, v)],
            params=params,
        ))

    for stretch in (1.5, 2.0, 2.5, 3.0, 4.0):
        out.append(Hypothesis(
            name=f"reversion(stretch={stretch})",
            build=lambda s=stretch: [MeanReversion(s)],
            params={"stretch_atr": stretch},
        ))

    out.append(Hypothesis(name="momentum", build=lambda: [VWAPMomentum()], params={}))

    # Combinations, because the interaction is part of the hypothesis.
    for minutes, stretch in itertools.product((15, 30), (2.0, 3.0)):
        out.append(Hypothesis(
            name=f"orb({minutes})+reversion({stretch})",
            build=lambda m=minutes, s=stretch: [OpeningRangeBreakout(m), MeanReversion(s)],
            params={"range_minutes": minutes, "stretch_atr": stretch},
        ))

    return out


# ── the verdict ─────────────────────────────────────────────────────────────

@dataclass
class Trial:
    hypothesis: str
    train: Metrics
    validation: Metrics
    params: dict = field(default_factory=dict)


@dataclass
class Verdict:
    trials: list[Trial]
    threshold: float
    split: Split
    survivor: Trial | None = None
    test: Metrics | None = None

    @property
    def survived(self) -> bool:
        return self.survivor is not None and self.test is not None and self.test.sharpe > 0

    def report(self) -> str:
        error = sharpe_standard_error(len(self.split.validation))
        required = days_needed(1.0, len(self.trials))
        lines = [
            f"  Hypotheses tested   {len(self.trials)}",
            f"  Data split          {self.split.summary()}",
            f"  Luck threshold      Sharpe {self.threshold:+.2f}  "
            f"(best-of-{len(self.trials)} expected from no skill at all)",
            f"  Measurement error   ±{error:.2f} Sharpe on "
            f"{len(self.split.validation)} validation days",
            "",
        ]
        if error > 1.0:
            lines += [
                "  ⚠ THIS SEARCH CANNOT REACH A CONCLUSION.",
                f"  The noise in a {len(self.split.validation)}-day Sharpe (±{error:.2f}) is larger",
                f"  than any edge worth having. To identify a Sharpe-1.0 strategy among",
                f"  {len(self.trials)} candidates you need about {required:,} validation days "
                f"(~{required / TRADING_DAYS_PER_YEAR:.0f} years).",
                "  Every result below is reported for completeness, not as evidence.",
                "",
            ]
        ranked = sorted(self.trials, key=lambda t: t.validation.sharpe, reverse=True)
        lines.append("  Best five by validation Sharpe:")
        for trial in ranked[:5]:
            beat = "CLEARS" if trial.validation.sharpe > self.threshold else "below luck"
            lines.append(
                f"    {trial.hypothesis:<34} train {trial.train.sharpe:>+6.2f}  "
                f"val {trial.validation.sharpe:>+6.2f}  net ₹{trial.validation.net:>+9,.0f}  {beat}"
            )
        lines.append("")

        if self.survivor is None:
            lines += [
                "  VERDICT: nothing survived.",
                "  No hypothesis beat what the best of this many random tries would",
                "  have produced. That is the honest result, and it is the common one.",
            ]
            return "\n".join(lines)

        lines.append(f"  Survivor: {self.survivor.hypothesis}")
        if self.test:
            lines += [
                f"  Held-out test (read once): Sharpe {self.test.sharpe:+.2f}, "
                f"net ₹{self.test.net:+,.0f}, {self.test.trades} trades",
                "",
            ]
            if self.survived:
                lines += [
                    "  VERDICT: survived selection and held up out of sample.",
                    "  This earns PAPER TRADING, not capital. One clean test is a",
                    "  reason to keep looking, not proof of an edge.",
                ]
            else:
                lines += [
                    "  VERDICT: cleared the search, then failed the held-out test.",
                    "  This is exactly what the third split exists to catch — it looked",
                    "  good on validation because the search had been reading validation.",
                ]
        return "\n".join(lines)


# ── the search ──────────────────────────────────────────────────────────────

def search(symbols: list[str] | None = None, *, capital: float = 100_000.0,
           interval: str = "5m", days: int = 60, workdir: str = ".earner",
           verbose: bool = True) -> Verdict:
    """Run every hypothesis, then let at most one of them touch the test set."""
    from pathlib import Path

    from .backtest import DEFAULT_UNIVERSE, replay
    from .marketdata import load_universe

    symbols = symbols or DEFAULT_UNIVERSE
    if verbose:
        print(f"Loading real NSE {interval} bars for {len(symbols)} symbols…")
    data = load_universe(symbols, interval=interval, days=days,
                         cache_dir=Path(workdir) / "cache")
    if not data:
        raise RuntimeError("no market data could be fetched")

    all_days = sorted({d for series in data.values() for d in series})
    split = split_days(all_days)
    space = hypotheses()

    if verbose:
        print(f"{len(all_days)} trading days · {split.summary()}")
        print(f"Testing {len(space)} hypotheses. The test set stays sealed until the end.\n")

    trials: list[Trial] = []
    for i, hypothesis in enumerate(space, 1):
        strategies = hypothesis.strategies()
        train_daily, train_trades = replay(data, split.train, strategies,
                                           capital=capital, workdir=workdir)
        val_daily, val_trades = replay(data, split.validation, strategies,
                                       capital=capital, workdir=workdir)
        trial = Trial(
            hypothesis=hypothesis.name,
            train=measure(train_daily, capital, train_trades),
            validation=measure(val_daily, capital, val_trades),
            params=hypothesis.params,
        )
        trials.append(trial)
        if verbose:
            print(f"  [{i:>2}/{len(space)}] {hypothesis.name:<34} "
                  f"train {trial.train.sharpe:>+6.2f}  val {trial.validation.sharpe:>+6.2f}")

    # The bar is what luck would have produced over this many tries.
    threshold = expected_max_sharpe(len(trials), len(split.validation))
    verdict = Verdict(trials=trials, threshold=threshold, split=split)

    clears = [t for t in trials if t.validation.sharpe > threshold]
    if not clears:
        return verdict

    # Exactly one candidate, and exactly one look at the test set.
    verdict.survivor = max(clears, key=lambda t: t.validation.score)
    winner = next(h for h in space if h.name == verdict.survivor.hypothesis)
    test_daily, test_trades = replay(data, split.test, winner.strategies(),
                                     capital=capital, workdir=workdir)
    verdict.test = measure(test_daily, capital, test_trades)
    return verdict

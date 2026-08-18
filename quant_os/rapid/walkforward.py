"""Walk-forward evaluation with purging, because in-sample means nothing.

Every number in the deliverables that describes model quality comes through
here. The design points that matter:

* **Folds are cut on day boundaries, not row boundaries.** Setups within a day
  overlap: a 30-minute breakout at 11:00 and a 45-minute continuation at 11:15
  share bars, so their outcomes are correlated. Splitting mid-day puts two
  views of the same price move on opposite sides of the train/test line, which
  is leakage that raises AUC and cannot be seen in the result.
* **An embargo day separates train from test.** A setup detected on the last
  training day can resolve into the first test day. One day of embargo removes
  the overlap entirely at the cost of one day per fold.
* **The model is refitted from scratch each fold.** Carrying weights forward
  would let the earliest fold's fit influence the last fold's test.
* **Anchored and rolling are both reported.** Anchored (expanding window) uses
  everything; rolling uses a fixed recent window. If they disagree the edge is
  non-stationary, which is worth knowing and is invisible if only one is run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from earner.trading.risk import IST
from quant_os.models.ensemble import Ensemble, score


def day_of(outcome) -> str:
    return datetime.fromtimestamp(outcome.at, IST).strftime("%Y-%m-%d")


@dataclass
class Fold:
    index: int
    train_days: int
    test_days: int
    train_rows: int
    test_rows: int
    metrics: dict = field(default_factory=dict)
    predictions: list = field(default_factory=list)   # (probability, outcome)


@dataclass
class WalkForward:
    folds: list = field(default_factory=list)
    mode: str = "anchored"

    @property
    def predictions(self) -> list:
        return [p for fold in self.folds for p in fold.predictions]

    def pooled(self) -> dict:
        """Out-of-sample metrics over every test fold together.

        Pooling is legitimate here and only here: each prediction was made by a
        model that had not seen its own row, so the pool is one honest
        out-of-sample sample rather than an average of averages.
        """
        pairs = self.predictions
        if not pairs:
            return {}
        from quant_os.models.logistic import auc, brier
        probabilities = [p for p, _ in pairs]
        labels = [o.win for _, o in pairs]
        return {
            "n": len(pairs),
            "base_rate": sum(labels) / len(labels),
            "auc": auc(probabilities, labels),
            "brier": brier(probabilities, labels),
            "brier_base": brier([sum(labels) / len(labels)] * len(labels), labels),
        }


def run(outcomes: list, *, folds: int = 6, mode: str = "anchored",
        embargo_days: int = 1, minimum_train: int = 400) -> WalkForward:
    """Walk the sample forward, refitting each fold. Returns every test prediction."""
    by_day = {}
    for outcome in outcomes:
        by_day.setdefault(day_of(outcome), []).append(outcome)
    days = sorted(by_day)
    if len(days) < folds * 3:
        folds = max(2, len(days) // 4)
    if folds < 2:
        return WalkForward(mode=mode)

    result = WalkForward(mode=mode)
    start = len(days) // (folds + 1)
    step = max(1, (len(days) - start) // folds)

    for i in range(folds):
        split = start + i * step
        test_end = min(split + step, len(days))
        if split >= len(days) or test_end <= split:
            break
        train_days = days[:split - embargo_days] if mode == "anchored" else \
            days[max(0, split - embargo_days - step * 3):split - embargo_days]
        test_days = days[split:test_end]
        if not train_days or not test_days:
            continue

        train = [o for d in train_days for o in by_day[d]]
        test = [o for d in test_days for o in by_day[d]]
        if len(train) < minimum_train or not test:
            continue

        model = Ensemble().fit(
            [o.features for o in train], [o.win for o in train],
            returns=[o.net_return for o in train],
            maes=[o.mae for o in train], mfes=[o.mfe for o in train])

        fold = Fold(index=i, train_days=len(train_days), test_days=len(test_days),
                    train_rows=len(train), test_rows=len(test))
        fold.metrics = score(model, [o.features for o in test], [o.win for o in test])
        fold.predictions = [(model.probability(o.features), o) for o in test]
        # The fitted model is kept out of the result on purpose: a Fold that
        # carries a model invites someone to use the last fold's model on the
        # sample it was fitted on.
        result.folds.append(fold)

    return result


def selectivity_curve(pairs: list, *, steps: int = 10) -> list:
    """Net return as a function of how selective the model is allowed to be.

    (threshold, trades, win rate, mean net return, total net). The single most
    informative table in the whole system: if the model has any skill, mean net
    return rises as the threshold rises. If the curve is flat, the model ranks
    nothing, and no amount of gating will make the strategy profitable.
    """
    if not pairs:
        return []
    probabilities = sorted(p for p, _ in pairs)
    out = []
    for i in range(steps):
        q = i / steps
        threshold = probabilities[int(q * (len(probabilities) - 1))]
        kept = [(p, o) for p, o in pairs if p >= threshold]
        if len(kept) < 10:
            continue
        nets = [o.net_return for _, o in kept]
        out.append((round(threshold, 4), len(kept),
                    sum(o.win for _, o in kept) / len(kept),
                    sum(nets) / len(nets), sum(nets)))
    return out

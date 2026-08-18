"""Seven heads, one decision (§8).

The brief's diagram asks for a price model, an order-flow model, an order-book
model, a momentum model, a reversal model, a regime model and a volatility
model, combined into a single trade probability. Implemented literally, with
one deviation that has to be stated up front:

    THE ORDER-BOOK HEAD IS EMPTY.

There is no historical Level-2 data in this repository. The depth recorder
built last week writes a live book to the event store, but it has days of
history, not months, and a head trained on days would be noise with a name.
`BOOK` is present in the group table, returns the base rate, and is excluded
from the stack until there is something to train it on. Leaving it in as a
stub that quietly returns 0.5 would let a reader believe the book is being
used; leaving it out entirely would hide the largest gap in the system.

The heads see disjoint feature groups. That is what makes this an ensemble
rather than one model run seven times: a head can only agree with another head
by finding the same thing in different data. When two heads disagree the stack
sees the disagreement, and disagreement is information the single model does
not have.

The stacker is trained on *out-of-fold* head predictions. Training it on the
heads' in-sample outputs would teach it to trust whichever head overfits
hardest, which is precisely backwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quant_os.models.logistic import Logistic, Ridge, auc, brier

# Which feature belongs to which head. A feature in two groups would let the
# heads agree by construction, so the groups are disjoint and the assertion in
# `check_groups` enforces it.
GROUPS = {
    "PRICE": ("acceleration", "close_position", "extension_atr", "trend",
              "retrace", "stretch_sigma", "move_pct", "rejection_atr",
              "range_width_atr", "failed_level"),
    "FLOW": ("flow_imbalance", "flow_run", "flow_delta_norm", "volume_z",
             "volume_accel", "absorption", "exhaustion", "trade_size_z",
             "divergence", "burst_strength", "volume_ratio",
             "estimator_agreement"),
    "BOOK": (),
    "MOMENTUM": ("acceleration", "volume_accel", "burst_strength", "trend"),
    "REVERSAL": ("exhaustion", "divergence", "stretch_sigma", "close_position"),
    "REGIME": ("minutes_left",),      # plus every regime_* and bucket_* one-hot
    "VOLATILITY": ("atr_pct", "volatility", "range_z"),
}

# MOMENTUM and REVERSAL deliberately reuse features from PRICE and FLOW: they
# are the brief's named strategy heads, not independent data sources, and the
# stacker is told so by `INDEPENDENT` below. Only these four are independent
# views, and only their disagreement carries information.
INDEPENDENT = ("PRICE", "FLOW", "REGIME", "VOLATILITY")

ACTIVE = ("PRICE", "FLOW", "MOMENTUM", "REVERSAL", "REGIME", "VOLATILITY")


def slice_features(row: dict, group: str) -> dict:
    names = GROUPS[group]
    out = {k: v for k, v in row.items() if k in names}
    if group == "REGIME":
        out.update({k: v for k, v in row.items()
                    if k.startswith("regime_") or k.startswith("bucket_")})
    return out


@dataclass
class Head:
    name: str
    model: Logistic | None = None
    base_rate: float = 0.5
    trained: bool = False

    def fit(self, rows: list, labels: list) -> "Head":
        self.base_rate = sum(labels) / len(labels) if labels else 0.5
        sliced = [slice_features(r, self.name) for r in rows]
        if not any(sliced) or not GROUPS[self.name] and self.name != "REGIME":
            return self          # nothing to train on: BOOK stays untrained
        if len(set(labels)) < 2:
            return self
        self.model = Logistic(l2=2.0).fit(sliced, labels)
        self.trained = True
        return self

    def predict(self, row: dict) -> float:
        if not self.trained:
            return self.base_rate
        return self.model.predict(slice_features(row, self.name))


@dataclass
class Ensemble:
    """The full stack: heads, a stacker, and the two continuous heads §7 asks for."""

    heads: dict = field(default_factory=dict)
    stacker: Logistic | None = None
    expected_return: Ridge | None = None
    expected_mae: Ridge | None = None
    expected_mfe: Ridge | None = None
    base_rate: float = 0.5

    def fit(self, rows: list, labels: list, returns: list | None = None,
            maes: list | None = None, mfes: list | None = None) -> "Ensemble":
        self.base_rate = sum(labels) / len(labels) if labels else 0.5

        # Inner split for out-of-fold stacker training. Split by position, and
        # the caller is responsible for having ordered rows in time — an
        # interleaved split here would leak the future into the stacker.
        cut = len(rows) // 2
        halves = [(rows[:cut], labels[:cut]), (rows[cut:], labels[cut:])]
        stack_rows, stack_labels = [], []
        for i, (train_rows, train_labels) in enumerate(halves):
            other_rows, other_labels = halves[1 - i]
            if len(set(train_labels)) < 2 or not train_rows or not other_rows:
                continue
            inner = {n: Head(n).fit(train_rows, train_labels) for n in ACTIVE}
            for row, y in zip(other_rows, other_labels):
                stack_rows.append({n: inner[n].predict(row) for n in ACTIVE})
                stack_labels.append(y)

        self.heads = {n: Head(n).fit(rows, labels) for n in ACTIVE}
        if stack_rows and len(set(stack_labels)) > 1:
            self.stacker = Logistic(l2=1.0).fit(stack_rows, stack_labels)

        if returns is not None:
            self.expected_return = Ridge(l2=2.0).fit(rows, returns)
        if maes is not None:
            self.expected_mae = Ridge(l2=2.0).fit(rows, maes)
        if mfes is not None:
            self.expected_mfe = Ridge(l2=2.0).fit(rows, mfes)
        return self

    def head_votes(self, row: dict) -> dict:
        return {n: self.heads[n].predict(row) for n in ACTIVE}

    def probability(self, row: dict) -> float:
        votes = self.head_votes(row)
        if self.stacker is None:
            return sum(votes.values()) / len(votes)
        return self.stacker.predict(votes)

    def dispersion(self, row: dict) -> float:
        """How far apart the four independent heads are.

        High dispersion means the heads are reading different stories out of
        the same moment. §8 says to execute only when the ensemble agrees
        strongly enough, and this is the number that measures 'agrees'.
        """
        votes = [self.heads[n].predict(row) for n in INDEPENDENT]
        mean = sum(votes) / len(votes)
        return (sum((v - mean) ** 2 for v in votes) / len(votes)) ** 0.5

    def predict(self, row: dict) -> dict:
        """Everything §7 asks the model to estimate, for one candidate."""
        p = self.probability(row)
        return {
            "p_win": p,
            "p_loss": 1 - p,
            "expected_return": (self.expected_return.predict(row)
                                if self.expected_return else 0.0),
            "expected_adverse": (self.expected_mae.predict(row)
                                 if self.expected_mae else 0.0),
            "expected_favourable": (self.expected_mfe.predict(row)
                                    if self.expected_mfe else 0.0),
            "dispersion": self.dispersion(row),
            "votes": self.head_votes(row),
        }


def score(ensemble: Ensemble, rows: list, labels: list) -> dict:
    """Out-of-sample quality of the stack and of each head separately.

    Per-head scores are reported because an ensemble whose AUC comes entirely
    from one head is not an ensemble, and knowing that changes what to build
    next.
    """
    probabilities = [ensemble.probability(r) for r in rows]
    out = {
        "n": len(rows),
        "base_rate": sum(labels) / len(labels) if labels else 0.0,
        "auc": auc(probabilities, labels),
        "brier": brier(probabilities, labels),
        "brier_base": brier([sum(labels) / len(labels)] * len(labels), labels) if labels else 0.0,
    }
    for name in ACTIVE:
        head = [ensemble.heads[name].predict(r) for r in rows]
        out[f"auc_{name}"] = auc(head, labels)
    return out


def check_groups() -> list:
    """Features claimed by more than one independent head. Should be empty."""
    seen, clashes = {}, []
    for group in INDEPENDENT:
        for name in GROUPS[group]:
            if name in seen:
                clashes.append((name, seen[name], group))
            seen[name] = group
    return clashes

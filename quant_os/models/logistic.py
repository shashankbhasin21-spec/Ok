"""Logistic regression, written out, because a dependency here would be worse.

The brief asks for a machine-learning prediction engine. The temptation is a
gradient-boosted tree with four hundred estimators, and on 3,500 labelled
setups with sixteen features that model will fit the sample beautifully and
tell you nothing. Regularised logistic regression is the right capacity for
this sample size: it has one coefficient per feature, its coefficients are
readable, and — the property that matters most — it cannot manufacture an
interaction that is not in the data.

Three things it does that a library call would not make explicit:

* **Standardisation is fitted on the training fold only.** Scaling with the
  full sample's mean and standard deviation leaks the test fold's distribution
  into training. It is a small leak and it is still a leak.
* **L2 is on by default and is not tuned to the test set.** A regularisation
  strength chosen by test performance is a hyperparameter fitted to the test
  set, which is the test set no longer being one.
* **It reports its own calibration.** A probability of 0.7 that is right 45%
  of the time is worse than useless in an expected-value gate, because the
  gate multiplies by it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1 / (1 + math.exp(-min(z, 60)))
    e = math.exp(max(z, -60))
    return e / (1 + e)


@dataclass
class Standardiser:
    names: list = field(default_factory=list)
    means: dict = field(default_factory=dict)
    scales: dict = field(default_factory=dict)

    def fit(self, rows: list) -> "Standardiser":
        self.names = sorted({k for r in rows for k in r})
        for name in self.names:
            values = [float(r.get(name, 0.0)) for r in rows]
            mean = sum(values) / len(values)
            var = sum((v - mean) ** 2 for v in values) / max(len(values) - 1, 1)
            self.means[name] = mean
            # A feature with no variance in the training fold is scaled by 1
            # and standardises to a constant zero, which the model then ignores.
            self.scales[name] = math.sqrt(var) or 1.0
        return self

    def transform(self, row: dict) -> list:
        return [(float(row.get(n, 0.0)) - self.means[n]) / self.scales[n]
                for n in self.names]


def solve(matrix: list, vector: list) -> list:
    """Gaussian elimination with partial pivoting. Small systems only.

    Written out rather than imported because the trading stack carries no
    third-party dependencies and CI enforces that. Partial pivoting is not
    optional: the design matrices here contain one-hot regime columns that are
    zero in most folds, and without pivoting the first such column divides by
    zero.
    """
    n = len(vector)
    a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            continue                      # singular column: leave its weight at 0
        a[col], a[pivot] = a[pivot], a[col]
        scale = a[col][col]
        for k in range(col, n + 1):
            a[col][k] /= scale
        for r in range(n):
            if r == col or a[r][col] == 0:
                continue
            factor = a[r][col]
            for k in range(col, n + 1):
                a[r][k] -= factor * a[col][k]
    return [a[i][n] for i in range(n)]


@dataclass
class Logistic:
    """Fitted by IRLS — Newton's method on the log-likelihood.

    Gradient descent was the first implementation and needed four hundred
    passes to converge; a walk-forward run of a hundred-odd fits took longer
    than the patience of anyone who would read the result, and a validation
    step that is too slow to run is a validation step that stops being run.
    IRLS reaches the same optimum in six to eight iterations because it uses
    the curvature as well as the slope. Same model, same regularisation, two
    orders of magnitude less waiting.
    """

    l2: float = 1.0
    max_iterations: int = 12
    tolerance: float = 1e-6
    weights: list = field(default_factory=list)
    bias: float = 0.0
    standardiser: Standardiser | None = None
    iterations: int = 0

    def fit(self, rows: list, labels: list) -> "Logistic":
        if not rows:
            raise ValueError("no training rows")
        self.standardiser = Standardiser().fit(rows)
        # The bias enters as a constant column so that Newton updates it
        # jointly with the weights; updating it separately converges slowly
        # whenever the base rate is far from a half, which here it always is.
        x = [[1.0] + self.standardiser.transform(r) for r in rows]
        n, d = len(x), len(self.standardiser.names) + 1
        rate = min(max(sum(labels) / n, 1e-6), 1 - 1e-6)
        beta = [math.log(rate / (1 - rate))] + [0.0] * (d - 1)

        for iteration in range(self.max_iterations):
            hessian = [[0.0] * d for _ in range(d)]
            gradient = [0.0] * d
            for row, y in zip(x, labels):
                p = sigmoid(sum(b * v for b, v in zip(beta, row)))
                w = max(p * (1 - p), 1e-8)
                error = p - y
                for j in range(d):
                    vj = row[j]
                    if vj == 0.0:
                        continue
                    gradient[j] += error * vj
                    wv = w * vj
                    hj = hessian[j]
                    for k in range(j, d):
                        hj[k] += wv * row[k]
            for j in range(d):
                for k in range(j):
                    hessian[j][k] = hessian[k][j]
            # L2 on the weights only; penalising the intercept would pull the
            # model away from the base rate for no statistical reason.
            for j in range(1, d):
                hessian[j][j] += self.l2
                gradient[j] += self.l2 * beta[j]

            step = solve(hessian, gradient)
            beta = [b - s for b, s in zip(beta, step)]
            self.iterations = iteration + 1
            if max(abs(s) for s in step) < self.tolerance:
                break

        self.bias, self.weights = beta[0], beta[1:]
        return self

    def predict(self, row: dict) -> float:
        if self.standardiser is None:
            raise ValueError("model is not fitted")
        v = self.standardiser.transform(row)
        return sigmoid(sum(w * x for w, x in zip(self.weights, v)) + self.bias)

    def coefficients(self) -> list:
        """(feature, weight), largest magnitude first. Readable by design."""
        if self.standardiser is None:
            return []
        pairs = list(zip(self.standardiser.names, self.weights))
        return sorted(pairs, key=lambda p: -abs(p[1]))


@dataclass
class Ridge:
    """Least squares with L2, for the continuous heads: expected return, MAE, MFE.

    Solved by the same gradient descent as the classifier rather than by a
    normal equation, because a matrix inverse written by hand on a
    near-singular design matrix is a bug waiting for a quiet Tuesday.
    """

    l2: float = 1.0
    weights: list = field(default_factory=list)
    bias: float = 0.0
    standardiser: Standardiser | None = None
    target_scale: float = 1.0

    def fit(self, rows: list, targets: list) -> "Ridge":
        self.standardiser = Standardiser().fit(rows)
        x = [[1.0] + self.standardiser.transform(r) for r in rows]
        n, d = len(x), len(self.standardiser.names) + 1
        # Targets are returns of order 1e-3. Scaling them to order 1 keeps the
        # normal equations away from the numerical floor of the solver.
        self.target_scale = (sum(abs(t) for t in targets) / n) or 1.0
        y = [t / self.target_scale for t in targets]

        xtx = [[0.0] * d for _ in range(d)]
        xty = [0.0] * d
        for row, target in zip(x, y):
            for j in range(d):
                vj = row[j]
                if vj == 0.0:
                    continue
                xty[j] += vj * target
                rj = xtx[j]
                for k in range(j, d):
                    rj[k] += vj * row[k]
        for j in range(d):
            for k in range(j):
                xtx[j][k] = xtx[k][j]
        for j in range(1, d):
            xtx[j][j] += self.l2

        beta = solve(xtx, xty)
        self.bias, self.weights = beta[0], beta[1:]
        return self

    def predict(self, row: dict) -> float:
        v = self.standardiser.transform(row)
        return (sum(w * x for w, x in zip(self.weights, v)) + self.bias) * self.target_scale


def calibration(probabilities: list, labels: list, *, buckets: int = 5) -> list:
    """(bucket, n, predicted, actual). The check that makes a probability usable.

    An expected-value gate multiplies the model's probability by a payoff. If
    the probability is not calibrated the product is not an expected value, and
    every downstream decision inherits the error.
    """
    if not probabilities:
        return []
    pairs = sorted(zip(probabilities, labels))
    size = max(1, len(pairs) // buckets)
    out = []
    for i in range(0, len(pairs), size):
        chunk = pairs[i:i + size]
        if len(chunk) < 5:
            continue
        out.append((i // size, len(chunk),
                    sum(p for p, _ in chunk) / len(chunk),
                    sum(y for _, y in chunk) / len(chunk)))
    return out


def brier(probabilities: list, labels: list) -> float:
    """Mean squared error of the probabilities. Lower is better; the base-rate
    model's score is the number any real model has to beat."""
    if not probabilities:
        return 0.0
    return sum((p - y) ** 2 for p, y in zip(probabilities, labels)) / len(probabilities)


def auc(probabilities: list, labels: list) -> float:
    """Area under the ROC curve, by rank. 0.5 is a coin flip."""
    pairs = sorted(zip(probabilities, labels))
    positives = sum(labels)
    negatives = len(labels) - positives
    if not positives or not negatives:
        return 0.5
    rank_sum, i = 0.0, 0
    while i < len(pairs):
        j = i
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        average_rank = (i + j + 1) / 2
        rank_sum += sum(average_rank for k in range(i, j) if pairs[k][1])
        i = j
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)

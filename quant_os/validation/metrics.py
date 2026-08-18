"""Probabilistic and deflated Sharpe ratios.

A Sharpe ratio computed from a short sample is a point estimate with an
enormous error bar, and comparing it to zero is the wrong test. These answer
the questions that actually matter:

* **PSR** — given this many observations, with this skew and this fat a tail,
  what is the probability the true Sharpe exceeds a benchmark?
* **DSR** — the same question with the benchmark set to what the best of N
  trials would produce by luck, which is the only honest benchmark after a
  search.

Both follow Bailey and López de Prado. Non-annualised per-period Sharpe is used
throughout because annualising multiplies the estimate *and* its error by the
same factor and changes nothing about the inference.
"""

from __future__ import annotations

import math
import statistics

EULER_MASCHERONI = 0.5772156649


def normal_cdf(z: float) -> float:
    """Φ(z), via erf. No dependency, accurate to double precision."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def inverse_normal_cdf(p: float) -> float:
    """Φ⁻¹(p). Acklam's rational approximation, ~1e-9."""
    if not 0.0 < p < 1.0:
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
    q, r = p - 0.5, (p - 0.5) ** 2
    return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5]) * q / \
           (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1)


def sharpe(returns: list[float]) -> float:
    """Per-period Sharpe at a zero risk-free rate. Not annualised."""
    if len(returns) < 2:
        return 0.0
    deviation = statistics.pstdev(returns)
    return statistics.mean(returns) / deviation if deviation else 0.0


def skewness(returns: list[float]) -> float:
    n = len(returns)
    if n < 3:
        return 0.0
    mean, deviation = statistics.mean(returns), statistics.pstdev(returns)
    if not deviation:
        return 0.0
    return sum(((r - mean) / deviation) ** 3 for r in returns) / n


def kurtosis(returns: list[float]) -> float:
    """Non-excess (normal = 3.0), which is the convention PSR expects."""
    n = len(returns)
    if n < 4:
        return 3.0
    mean, deviation = statistics.mean(returns), statistics.pstdev(returns)
    if not deviation:
        return 3.0
    return sum(((r - mean) / deviation) ** 4 for r in returns) / n


def probabilistic_sharpe(returns: list[float], benchmark: float = 0.0) -> float:
    """P(true Sharpe > benchmark), accounting for sample size, skew and tails.

    Negative skew and fat tails both make a given Sharpe less believable, which
    is why a strategy that wins small and often but loses catastrophically
    scores worse here than its raw Sharpe suggests.
    """
    n = len(returns)
    if n < 4:
        return 0.0
    observed = sharpe(returns)
    variance = 1 - skewness(returns) * observed + \
        (kurtosis(returns) - 1) / 4 * observed ** 2
    if variance <= 0:
        return 0.0
    return normal_cdf((observed - benchmark) * math.sqrt(n - 1) / math.sqrt(variance))


def expected_max_sharpe(trials: int, observations: int,
                        variance_of_sharpes: float | None = None) -> float:
    """Per-period Sharpe the best of `trials` worthless strategies would show.

    Taking a maximum over many draws produces a respectable number with no
    skill involved. This is what a candidate must beat.
    """
    if trials < 2 or observations < 2:
        return 0.0
    spread = math.sqrt(variance_of_sharpes) if variance_of_sharpes is not None \
        else 1.0 / math.sqrt(observations)
    z_high = inverse_normal_cdf(1 - 1 / trials)
    z_low = inverse_normal_cdf(1 - 1 / (trials * math.e))
    return spread * ((1 - EULER_MASCHERONI) * z_high + EULER_MASCHERONI * z_low)


def deflated_sharpe(returns: list[float], trials: int,
                    variance_of_sharpes: float | None = None) -> float:
    """PSR against the luck benchmark. The number to promote on, if any.

    Below ~0.95 the honest reading is "this could easily be the best of N
    coin flips", regardless of how good the equity curve looks.
    """
    benchmark = expected_max_sharpe(trials, len(returns), variance_of_sharpes)
    return probabilistic_sharpe(returns, benchmark)


def minimum_track_record(returns: list[float], benchmark: float = 0.0,
                         confidence: float = 0.95) -> float:
    """Observations needed before a Sharpe this size is believable.

    The answer to "how long must I paper trade before this means anything",
    which is otherwise decided by impatience.
    """
    observed = sharpe(returns)
    if observed <= benchmark:
        return float("inf")
    variance = 1 - skewness(returns) * observed + \
        (kurtosis(returns) - 1) / 4 * observed ** 2
    if variance <= 0:
        return float("inf")
    z = inverse_normal_cdf(confidence)
    return 1 + variance * (z / (observed - benchmark)) ** 2

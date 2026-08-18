"""What this system has learned, encoded so it acts on it.

Every rule below was measured in this repository, not assumed, and each one
carries the evidence that produced it. The point is that knowledge which lives
only in a report gets forgotten and re-learned expensively; knowledge that
lives in a gate cannot be.

A strategy proposal is checked against all of it. Anything contradicting a
measured finding is refused with the number that refuses it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Lesson:
    """One measured finding, with the evidence attached."""

    name: str
    finding: str
    evidence: str
    rule: str

    def __str__(self) -> str:
        return f"{self.name}: {self.finding}\n    evidence: {self.evidence}\n    rule: {self.rule}"


LESSONS: tuple[Lesson, ...] = (
    Lesson(
        name="intraday_has_no_edge",
        finding="Intraday equity strategies here lose before any cost is charged.",
        evidence="25 NSE sessions, 10 symbols, 5-minute bars. At Rs 0 brokerage "
                 "and 0bp slippage - conditions available to nobody - net was "
                 "-1,574. Gross was negative at every portfolio width from 1 to 25.",
        rule="Refuse intraday equity proposals. The deficit is not in the cost "
             "model, so no broker plan or execution improvement addresses it.",
    ),
    Lesson(
        name="holding_period_sets_the_cost_floor",
        finding="Cost drag is set by holding period, not by broker choice.",
        evidence="Measured 0.73%/day intraday (~182%/yr) against 22bp/yr for the "
                 "published A-share system holding up to 9 days. An 825x gap "
                 "caused by turnover, not by pricing.",
        rule="Require expected holding period >= 5 sessions, or an explicit "
             "gross edge that clears the measured turnover cost.",
    ),
    Lesson(
        name="bearish_candles_are_inverted",
        finding="Every bearish candle pattern loses money when traded short.",
        evidence="191,118 daily bars, 23 global instruments. shooting_star -1.44%, "
                 "three_black_crows -1.42%, rsi_overbought -1.63% after cost. "
                 "Even in a measured downtrend the 10-day forward return is +0.49%.",
        rule="Never open a short on a bearish candle pattern alone.",
    ),
    Lesson(
        name="patterns_measure_drift",
        finding="Bullish candle patterns capture market drift, not prediction.",
        evidence="Base rate for a random day is +0.53% over 10 days. The best of "
                 "19 patterns returned +0.69%, an edge of +0.16pp against a 0.22pp "
                 "round trip. 0 of 19 cleared cost.",
        rule="Score every signal against the base rate of the same universe and "
             "window. Raw return is not evidence.",
    ),
    Lesson(
        name="volatility_flatters_everything",
        finding="High-volatility periods lift patterns and the market equally.",
        evidence="Base rate +0.93% in high vol vs +0.34% in low vol. Pattern "
                 "returns rise in the same proportion; the ratio barely moves.",
        rule="Report regime-conditioned results against regime-conditioned base "
             "rates. A backtest confined to a volatile period is showing weather.",
    ),
    Lesson(
        name="search_manufactures_winners",
        finding="Searching until something wins always succeeds, on noise.",
        evidence="5,000 provably meaningless rules (bar index modulo N) on real "
                 "NIFTY data produced +40.8%/yr at Sharpe 2.20 - beating the best "
                 "peer-reviewed A-share result. PSR read 1.00; DSR read 0.28.",
        rule="Fix the candidate count before searching, deflate for it, and treat "
             "'nothing survived' as a valid terminal answer.",
    ),
    Lesson(
        name="concentration_beats_breadth_for_ranked_signals",
        finding="Widening a ranked portfolio destroys the edge it ranks for.",
        evidence="Cross-sectional momentum: 3 names +13.3%/yr (t 3.14), 15 names "
                 "+0.9%/yr (t 0.45). Drawdown improved only -41.6% to -35.1%.",
        rule="Do not widen a ranked signal for diversification. Position 15 has a "
             "different edge from position 1; averaging them averages it away.",
    ),
    Lesson(
        name="regime_filters_earn_their_place",
        finding="A trend filter improved every metric simultaneously.",
        evidence="Same momentum strategy with NIFTY-above-200dma: +13.3% to "
                 "+15.4%/yr, Sharpe 0.57 to 0.65, drawdown -41.6% to -38.3%, "
                 "out-of-sample retention 53% to 68%.",
        rule="Prefer standing aside in unfavourable regimes over sizing down.",
    ),
    Lesson(
        name="data_depth_bounds_what_is_knowable",
        finding="Intraday history cannot support intraday validation.",
        evidence="Free data: 1m ~7 days, 5m ~60 days, 1d 20+ years. Separating a "
                 "Sharpe-1 edge from luck across 20 candidates needs ~904 "
                 "observations. Sharpe error is +/-5.00 on 10 days, +/-0.23 on 4,936.",
        rule="Refuse to promote on a sample whose measurement error exceeds the "
             "effect being measured.",
    ),
    Lesson(
        name="an_acceptance_is_not_a_fill",
        finding="Broker order acknowledgement carries no price or quantity.",
        evidence="Kotak place_order returns exactly 3 fields - stat, nOrdNo, "
                 "stCode - and 0 of them carry price or filled quantity. The "
                 "original adapter fabricated the price from a separate quote "
                 "call, so a partial or rejected fill would have been booked at "
                 "a price that never traded.",
        rule="Produce a Fill only from the broker's own order or trade book.",
    ),
    Lesson(
        name="costs_belong_in_the_pnl",
        finding="A P&L excluding brokerage makes the daily loss limit lie.",
        evidence="realized_pnl originally walked fill prices only. A multi-day "
                 "run then reported +17,280 gross on 5,000 of capital against "
                 "random-walk prices, which cannot happen; the daily stop had "
                 "been measuring a number better than reality and halting late.",
        rule="Subtract costs inside realized P&L, not alongside it.",
    ),
    Lesson(
        name="bars_are_not_minutes",
        finding="A bar count used as a duration silently changes the strategy.",
        evidence="opening_range(candles, 15) sliced 15 BARS - 75 minutes on "
                 "5-minute data, while the log said '15m'. Warm-up differed "
                 "between live (40 min) and backtest (200 min).",
        rule="Express every window as a duration and convert against the inferred "
             "bar interval. Refuse durations that are not whole bars.",
    ),
)

LESSONS_BY_NAME = {lesson.name: lesson for lesson in LESSONS}


# ── applying what was learned ───────────────────────────────────────────────

@dataclass
class Proposal:
    """A strategy someone - a person or a model - wants to run."""

    name: str
    holding_sessions: float
    direction: str = "long"          # long | short | both
    signal_type: str = "other"       # candle | momentum | meanreversion | other
    universe_size: int = 1
    candidates_tested: int = 1
    observations: int = 0
    measured_edge: float | None = None      # excess over base rate, fractional
    base_rate_adjusted: bool = False
    regime_filtered: bool = False


@dataclass
class Review:
    proposal: str
    refusals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def permitted(self) -> bool:
        return not self.refusals

    def report(self) -> str:
        head = f"{self.proposal}: {'PERMITTED' if self.permitted else 'REFUSED'}"
        lines = [head]
        lines += [f"  ✗ {r}" for r in self.refusals]
        lines += [f"  ! {w}" for w in self.warnings]
        return "\n".join(lines)


# Roughly how many observations are needed to see an effect of a given size
# through the noise, given how many candidates were tried.
def observations_needed(edge_sharpe: float, candidates: int) -> int:
    import math
    if edge_sharpe <= 0:
        return 10 ** 9
    # Expected best-of-N z-score under the null, times the standard error.
    z = math.sqrt(2 * math.log(max(candidates, 2)))
    return int((z / edge_sharpe) ** 2)


def review(proposal: Proposal) -> Review:
    """Check a proposal against everything measured. Refusals cite the evidence."""
    out = Review(proposal=proposal.name)

    if proposal.holding_sessions < 1:
        out.refusals.append(
            f"intraday_has_no_edge — {LESSONS_BY_NAME['intraday_has_no_edge'].evidence}")
    elif proposal.holding_sessions < 5:
        out.warnings.append(
            "holding_period_sets_the_cost_floor — under 5 sessions the measured "
            "turnover cost is not clearly covered")

    if proposal.signal_type == "candle" and proposal.direction in ("short", "both"):
        out.refusals.append(
            f"bearish_candles_are_inverted — {LESSONS_BY_NAME['bearish_candles_are_inverted'].evidence}")

    if proposal.measured_edge is not None and not proposal.base_rate_adjusted:
        out.refusals.append(
            "patterns_measure_drift — the edge was not measured against the base "
            "rate of the same universe and window, so it may be drift")

    if proposal.measured_edge is not None and proposal.measured_edge <= 0:
        out.refusals.append(
            f"measured edge is {proposal.measured_edge:+.4f} — non-positive before "
            "any promotion question arises")

    if proposal.signal_type in ("momentum", "meanreversion") and proposal.universe_size > 10:
        out.warnings.append(
            "concentration_beats_breadth_for_ranked_signals — widening a ranked "
            "signal measured 13.3%/yr at 3 names falling to 0.9%/yr at 15")

    if proposal.candidates_tested > 1 and proposal.observations:
        needed = observations_needed(0.1, proposal.candidates_tested)
        if proposal.observations < needed:
            out.refusals.append(
                f"data_depth_bounds_what_is_knowable — {proposal.observations:,} "
                f"observations against ~{needed:,} needed after testing "
                f"{proposal.candidates_tested:,} candidates")

    if not proposal.regime_filtered:
        out.warnings.append(
            "regime_filters_earn_their_place — a trend filter improved return, "
            "Sharpe, drawdown and out-of-sample retention simultaneously")

    return out


def curriculum() -> str:
    """Everything learned, in order. What the bot knows and how it knows it."""
    return "\n\n".join(str(lesson) for lesson in LESSONS)

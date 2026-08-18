"""What a round trip on an Indian exchange actually costs.

Section 9 of the brief is an expected-value gate, and a gate is only as
honest as the number it subtracts. The previous cost model in this repository
was a single blended rate — 0.05% of turnover plus 3bp of slippage — which is
roughly right on a 300 lot of a 1,000 stock and badly wrong everywhere else,
because the real bill is a sum of six charges with different bases, different
sides and a flat cap on the largest one.

Every rate here is the published statutory or exchange rate for the equity
cash, index-futures and index-options segments. They change: SEBI revised
exchange transaction charges in October 2024, and STT on options in October
2024. They are constants in one place so that a revision is a one-line edit
rather than a hunt.

Three deliberate properties:

* **Per side, not per round trip.** STT falls on the sell leg in equity
  intraday and on the sell leg of a futures or options contract; stamp duty
  falls on the buy leg only. A model that charges half of a round-trip
  estimate to each leg gets the sign of the asymmetry wrong.
* **Brokerage is capped and therefore non-linear.** 20 or 0.03%, whichever
  is lower, means cost as a fraction of turnover *falls* with trade size. A
  percentage-only model overcharges large trades and — far worse — grossly
  undercharges small ones, which is the direction that manufactures edges.
* **Spread and impact are separate from fees.** Fees are known before the
  trade. Spread and impact are estimated, and estimates belong in a different
  field so that a result can be re-run against a worse assumption.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

BUY, SELL = "B", "S"

EQUITY_INTRADAY = "EQUITY_INTRADAY"
INDEX_FUTURES = "INDEX_FUTURES"
INDEX_OPTIONS = "INDEX_OPTIONS"


@dataclass(frozen=True)
class Schedule:
    """One segment's statutory bill, as fractions of turnover."""

    brokerage_rate: float          # of turnover, per executed order
    brokerage_cap: float           # rupees, per executed order
    stt_sell: float                # securities transaction tax, sell side
    stt_buy: float                 # zero in every intraday segment here
    exchange_rate: float           # NSE transaction charge, both sides
    sebi_rate: float = 0.000001    # 10 per crore
    ipft_rate: float = 0.0         # investor protection fund, NSE
    stamp_buy: float = 0.0         # state stamp duty, buy side only
    gst_rate: float = 0.18         # on brokerage + exchange + sebi


# Equity intraday (MIS). STT 0.025% on the sell leg; stamp 0.003% on the buy.
EQUITY = Schedule(
    brokerage_rate=0.0003, brokerage_cap=20.0,
    stt_sell=0.00025, stt_buy=0.0,
    exchange_rate=0.0000297, ipft_rate=0.000001,
    stamp_buy=0.00003,
)

# Index futures. STT 0.02% sell, exchange 0.00173%, stamp 0.002% buy.
FUTURES = Schedule(
    brokerage_rate=0.0003, brokerage_cap=20.0,
    stt_sell=0.0002, stt_buy=0.0,
    exchange_rate=0.0000173,
    stamp_buy=0.00002,
)

# Index options, charged on *premium* turnover. STT 0.1% on the sell leg,
# exchange 0.03503%, stamp 0.003% buy. The exchange charge here is an order of
# magnitude above cash, which is why option scalping needs a far larger move.
OPTIONS = Schedule(
    brokerage_rate=0.0, brokerage_cap=20.0,
    stt_sell=0.001, stt_buy=0.0,
    exchange_rate=0.0003503,
    stamp_buy=0.00003,
)

SCHEDULES = {EQUITY_INTRADAY: EQUITY, INDEX_FUTURES: FUTURES, INDEX_OPTIONS: OPTIONS}


@dataclass
class Charges:
    """One leg's bill, itemised. Itemised so a surprise can be located."""

    brokerage: float = 0.0
    stt: float = 0.0
    exchange: float = 0.0
    sebi: float = 0.0
    ipft: float = 0.0
    stamp: float = 0.0
    gst: float = 0.0

    @property
    def total(self) -> float:
        return round(self.brokerage + self.stt + self.exchange + self.sebi
                     + self.ipft + self.stamp + self.gst, 4)

    def __add__(self, other: "Charges") -> "Charges":
        return Charges(
            brokerage=self.brokerage + other.brokerage, stt=self.stt + other.stt,
            exchange=self.exchange + other.exchange, sebi=self.sebi + other.sebi,
            ipft=self.ipft + other.ipft, stamp=self.stamp + other.stamp,
            gst=self.gst + other.gst,
        )

    def itemise(self) -> str:
        return (f"brokerage {self.brokerage:.2f}  STT {self.stt:.2f}  "
                f"exchange {self.exchange:.2f}  SEBI {self.sebi:.2f}  "
                f"stamp {self.stamp:.2f}  GST {self.gst:.2f}  = {self.total:.2f}")


def charges(turnover: float, side: str, *, segment: str = EQUITY_INTRADAY) -> Charges:
    """The statutory bill for one executed order of this turnover."""
    s = SCHEDULES[segment]
    brokerage = min(turnover * s.brokerage_rate, s.brokerage_cap)
    stt = turnover * (s.stt_sell if side == SELL else s.stt_buy)
    exchange = turnover * s.exchange_rate
    sebi = turnover * s.sebi_rate
    ipft = turnover * s.ipft_rate
    stamp = turnover * (s.stamp_buy if side == BUY else 0.0)
    gst = (brokerage + exchange + sebi) * s.gst_rate
    return Charges(brokerage=brokerage, stt=stt, exchange=exchange, sebi=sebi,
                   ipft=ipft, stamp=stamp, gst=gst)


def round_trip(price: float, quantity: int, *, segment: str = EQUITY_INTRADAY,
               exit_price: float | None = None) -> Charges:
    """Both legs. The number that a per-trade edge has to clear."""
    entry = charges(price * quantity, BUY, segment=segment)
    out = charges((exit_price or price) * quantity, SELL, segment=segment)
    return entry + out


# ── spread and impact ───────────────────────────────────────────────────────
#
# Fees are known. These are estimated, and the estimate matters more than the
# fees for anything held for minutes: a 6bp round-trip spread on a mid-cap is
# three times the statutory bill.

def corwin_schultz_spread(highs: list[float], lows: list[float]) -> float:
    """Effective spread as a fraction of price, from high/low ranges alone.

    Corwin & Schultz (2012). Two consecutive bars' ranges contain both the
    asset's variance (which scales with the interval) and the spread (which
    does not), and the pair of equations separates them. It is the only way to
    get a spread estimate out of OHLC data, and it is what makes a cost model
    possible at all on a feed with no quotes in it.

    Returns 0.0 when the estimator goes negative, which it does on quiet bars
    — a negative spread is the estimator saying "smaller than I can resolve",
    not a rebate.
    """
    if len(highs) < 2 or len(lows) < 2:
        return 0.0
    estimates = []
    for i in range(len(highs) - 1):
        h1, l1, h2, l2 = highs[i], lows[i], highs[i + 1], lows[i + 1]
        if min(l1, l2) <= 0 or h1 <= 0 or h2 <= 0:
            continue
        beta = math.log(h1 / l1) ** 2 + math.log(h2 / l2) ** 2
        gamma = math.log(max(h1, h2) / min(l1, l2)) ** 2
        denom = 3 - 2 * math.sqrt(2)
        alpha = (math.sqrt(2 * beta) - math.sqrt(beta)) / denom - math.sqrt(gamma / denom)
        spread = 2 * (math.exp(alpha) - 1) / (1 + math.exp(alpha))
        estimates.append(max(spread, 0.0))
    if not estimates:
        return 0.0
    estimates.sort()
    return estimates[len(estimates) // 2]


def impact(quantity: int, price: float, *, bar_volume: float,
           volatility: float, coefficient: float = 0.6) -> float:
    """Price concession from taking liquidity, as a fraction of price.

    The square-root law: impact grows with the square root of participation,
    scaled by the instrument's own volatility. Almadi/Chriss and the empirical
    literature put the coefficient between 0.3 and 1.0; 0.6 is the middle and
    is deliberately not tuned, because tuning it is tuning the answer.

    Participation above 100% of the bar's volume returns a large number rather
    than an extrapolation: the model has left the range where it means anything.
    """
    if bar_volume <= 0 or price <= 0:
        return 0.05
    participation = (quantity * price) / (bar_volume * price)
    if participation >= 1.0:
        return 0.05
    return coefficient * max(volatility, 1e-6) * math.sqrt(participation)


@dataclass
class CostModel:
    """Everything charged against a trade, in one place.

    `slippage_floor` exists because the estimators above can return zero on a
    quiet series and a zero-cost fill is the single most common way a backtest
    invents an edge. One basis point per side is a floor, not an estimate.
    """

    segment: str = EQUITY_INTRADAY
    slippage_floor: float = 0.0001
    impact_coefficient: float = 0.6
    latency_bars: int = 1

    def per_share_cost(self, price: float, quantity: int, *, spread: float,
                       bar_volume: float, volatility: float) -> float:
        """Round-trip cost per share: fees plus half-spread and impact, twice."""
        if quantity <= 0 or price <= 0:
            return float("inf")
        fees = round_trip(price, quantity, segment=self.segment).total
        friction = max(spread / 2, self.slippage_floor) + impact(
            quantity, price, bar_volume=bar_volume, volatility=volatility,
            coefficient=self.impact_coefficient)
        return fees / quantity + 2 * friction * price

    def fill_price(self, reference: float, side: str, *, spread: float,
                   quantity: int, bar_volume: float, volatility: float) -> float:
        """Where a marketable order actually prints — always against you."""
        friction = max(spread / 2, self.slippage_floor) + impact(
            quantity, reference, bar_volume=bar_volume, volatility=volatility,
            coefficient=self.impact_coefficient)
        return reference * (1 + friction) if side == BUY else reference * (1 - friction)

    def fillable(self, quantity: int, bar_volume: float, *,
                 participation_cap: float = 0.10) -> int:
        """Partial fills. You do not get more than a slice of the bar's volume.

        Ten percent is the conventional ceiling for a participation algorithm
        and is generous for a single marketable order. Without this a backtest
        will happily buy the entire day's volume of a small-cap at the close.
        """
        if bar_volume <= 0:
            return 0
        return max(0, min(quantity, int(bar_volume * participation_cap)))

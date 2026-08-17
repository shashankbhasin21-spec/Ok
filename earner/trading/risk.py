"""Risk limits and the trade book.

These are not a safety blanket bolted onto a trading system — they *are* the
trading system. A strategy without position limits and a daily stop is not a
strategy, it is a way to find out how large a single loss can be.

The book keeps realized and unrealized P&L apart for the same reason the
earner ledger keeps settled cash apart from invoiced: an open position that is
currently up is not money you have made.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .broker import BUY, Fill

IST = timezone(timedelta(hours=5, minutes=30))

SCHEMA = """
CREATE TABLE IF NOT EXISTS fills (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   TEXT NOT NULL UNIQUE,
    symbol     TEXT NOT NULL,
    side       TEXT NOT NULL,
    quantity   INTEGER NOT NULL,
    price      REAL NOT NULL,
    paper      INTEGER NOT NULL,
    at         REAL NOT NULL,
    session    TEXT NOT NULL,
    cost       REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    kind    TEXT NOT NULL,
    data    TEXT NOT NULL,
    at      REAL NOT NULL,
    session TEXT NOT NULL
);
"""


def trading_day(when: float | None = None) -> str:
    return datetime.fromtimestamp(when or time.time(), IST).strftime("%Y-%m-%d")


@dataclass
class Position:
    symbol: str
    quantity: int          # signed: positive long, negative short
    average_price: float

    @property
    def is_open(self) -> bool:
        return self.quantity != 0


class Book:
    """Every fill, and honest arithmetic over them."""

    def __init__(self, path: Path | str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # Books written before costs were tracked are still readable; their
        # fills simply report zero cost rather than failing to load.
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(fills)")}
        if "cost" not in columns:
            self.conn.execute("ALTER TABLE fills ADD COLUMN cost REAL NOT NULL DEFAULT 0")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def record(self, fill: Fill) -> bool:
        """Store a fill. False if this order id was already recorded."""
        try:
            self.conn.execute(
                "INSERT INTO fills (order_id, symbol, side, quantity, price, paper, at,"
                " session, cost) VALUES (?,?,?,?,?,?,?,?,?)",
                (fill.order_id, fill.symbol, fill.side, fill.quantity, fill.price,
                 int(fill.paper), fill.at, trading_day(fill.at), fill.cost),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def note(self, kind: str, **data) -> None:
        self.conn.execute(
            "INSERT INTO notes (kind, data, at, session) VALUES (?,?,?,?)",
            (kind, json.dumps(data, default=str), time.time(), trading_day()),
        )
        self.conn.commit()

    def fills(self, session: str | None = None) -> list[sqlite3.Row]:
        if session:
            return list(self.conn.execute(
                "SELECT * FROM fills WHERE session=? ORDER BY id", (session,)))
        return list(self.conn.execute("SELECT * FROM fills ORDER BY id"))

    def positions(self, session: str | None = None) -> dict[str, Position]:
        """Net position per symbol, with a weighted average entry price."""
        out: dict[str, Position] = {}
        for row in self.fills(session):
            signed = row["quantity"] if row["side"] == BUY else -row["quantity"]
            pos = out.get(row["symbol"]) or Position(row["symbol"], 0, 0.0)

            if pos.quantity == 0 or (pos.quantity > 0) == (signed > 0):
                # Opening or adding: blend the entry price.
                total = pos.quantity + signed
                if total:
                    pos.average_price = (
                        pos.average_price * pos.quantity + row["price"] * signed
                    ) / total
                pos.quantity = total
            else:
                # Reducing or flipping: entry price survives until flat.
                pos.quantity += signed
                if pos.quantity == 0:
                    pos.average_price = 0.0
            out[row["symbol"]] = pos
        return out

    def costs(self, session: str | None = None) -> float:
        """Brokerage and charges paid. Per order, so they do not shrink with the account."""
        return round(sum(row["cost"] for row in self.fills(session)), 2)

    def gross_pnl(self, session: str | None = None) -> float:
        """Price movement only, before a rupee of brokerage. Never the number to
        judge a strategy by — it is what makes paper engines look profitable."""
        return self._matched_pnl(session)

    def realized_pnl(self, session: str | None = None) -> float:
        """Money actually made or lost on closed quantity, **after costs**.

        Costs are subtracted here rather than reported alongside, because this
        is the number the daily loss limit is measured against. Excluding them
        makes the limit stop later than it promised — on a small account, much
        later, since brokerage is charged per order and does not scale down.
        """
        return round(self._matched_pnl(session) - self.costs(session), 2)

    def _matched_pnl(self, session: str | None = None) -> float:
        """FIFO over fills. An open position contributes nothing here no matter
        how far in front it is."""
        lots: dict[str, list[tuple[int, float]]] = {}
        realized = 0.0
        for row in self.fills(session):
            symbol = row["symbol"]
            signed = row["quantity"] if row["side"] == BUY else -row["quantity"]
            price = row["price"]
            queue = lots.setdefault(symbol, [])

            while signed and queue and (queue[0][0] > 0) != (signed > 0):
                lot_qty, lot_price = queue[0]
                matched = min(abs(lot_qty), abs(signed))
                direction = 1 if lot_qty > 0 else -1
                realized += direction * matched * (price - lot_price)

                remaining = abs(lot_qty) - matched
                if remaining:
                    queue[0] = (direction * remaining, lot_price)
                else:
                    queue.pop(0)
                signed -= -direction * matched if signed > 0 else -direction * matched
                signed = signed if abs(signed) > 0 else 0
                if matched == abs(signed) + matched:
                    break
            if signed:
                queue.append((signed, price))
        return round(realized, 2)

    def unrealized_pnl(self, marks: dict[str, float], session: str | None = None) -> float:
        """Paper gain on positions still open. Not money."""
        total = 0.0
        for symbol, pos in self.positions(session).items():
            if pos.is_open and symbol in marks:
                total += pos.quantity * (marks[symbol] - pos.average_price)
        return round(total, 2)


# Positions inside a group move together, so the risk engine treats them as one
# exposure rather than as independent bets. Ten Nifty banks in a selloff is one
# trade taken ten times (spec §13).
CORRELATION_GROUPS: dict[str, tuple[str, ...]] = {
    "banking": ("HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK",
                "BANKNIFTY", "FEDERALBNK", "IDFCFIRSTB", "PNB", "BANKBARODA"),
    "it": ("TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "LTIM", "MPHASIS", "COFORGE"),
    "energy": ("RELIANCE", "ONGC", "IOC", "BPCL", "GAIL", "NTPC", "POWERGRID", "COALINDIA"),
    "auto": ("MARUTI", "TATAMOTORS", "M&M", "BAJAJ-AUTO", "EICHERMOT", "HEROMOTOCO", "TVSMOTOR"),
    "pharma": ("SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "AUROPHARMA", "LUPIN"),
    "fmcg": ("HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR", "MARICO"),
    "metals": ("TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "SAIL", "JINDALSTEL"),
    "index": ("NIFTY", "NIFTY50", "FINNIFTY", "MIDCPNIFTY", "SENSEX"),
}


def correlation_group(symbol: str) -> str:
    """Which exposure bucket a symbol belongs to. Unknown symbols stand alone."""
    key = symbol.upper().split("-")[0].strip()
    for group, members in CORRELATION_GROUPS.items():
        if any(key.startswith(m) for m in members):
            return group
    return f"single:{key}"


@dataclass
class PortfolioExposure:
    """What is actually at risk, after correlation is accounted for."""

    total_risk_pct: float
    by_group: dict[str, float]
    effective_bets: float      # how many genuinely independent positions you hold
    largest_group: str
    largest_group_pct: float


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    quantity: int = 0


class RiskManager:
    """The limits that decide whether an order is placed at all."""

    def __init__(
        self,
        capital: float,
        *,
        risk_per_trade: float = 0.01,      # 1% of capital at risk per position
        stop_loss_pct: float = 0.005,      # 0.5% adverse move closes the trade
        daily_loss_limit: float = 0.03,    # 3% down on the day and the engine stops
        max_positions: int = 3,
        max_trades_per_day: int = 15,
        # Total risk across everything open. Simulation puts the best median
        # return near 30% spread over independent positions; past that, return
        # falls AND ruin rises, so this is a ceiling rather than a suggestion.
        max_portfolio_risk: float = 0.30,
        max_group_risk: float = 0.10,      # per correlation bucket
        max_consecutive_losses: int = 4,
        # Gross notional as a multiple of capital. MIS gives roughly 5x on
        # liquid equity, and this is a hard wall rather than a preference: past
        # it the broker rejects the order for margin, which on a small account
        # arrives long before any risk limit does.
        max_leverage: float = 4.0,
    ):
        self.capital = capital
        self.risk_per_trade = risk_per_trade
        self.stop_loss_pct = stop_loss_pct
        self.daily_loss_limit = daily_loss_limit
        self.max_positions = max_positions
        self.max_trades_per_day = max_trades_per_day
        self.max_portfolio_risk = max_portfolio_risk
        self.max_group_risk = max_group_risk
        self.max_consecutive_losses = max_consecutive_losses
        self.max_leverage = max_leverage
        self.halted = False
        self.halt_reason = ""

    @classmethod
    def aggressive(cls, capital: float) -> "RiskManager":
        """The aggressive preset, set where the simulation says return peaks.

        Not "maximum": at 100% portfolio risk the median return collapses by
        ~88% and ruin becomes a coin flip. This sits at the top of the range
        that still grows.
        """
        return cls(
            capital,
            risk_per_trade=0.03,
            daily_loss_limit=0.06,
            max_positions=10,
            max_trades_per_day=30,
            max_portfolio_risk=0.30,
            max_group_risk=0.12,
        )

    @classmethod
    def diversified(cls, capital: float) -> "RiskManager":
        """Many small uncorrelated positions — the configuration that won.

        Same total risk as the aggressive preset, spread across more names. In
        simulation that single change moved the median from ₹45,828 to
        ₹112,537 and took ruin from meaningful to zero, because ten
        independent bets of 1% behave nothing like one bet of 10%.

        The catch on a small account is cost, not risk: ten positions is twenty
        orders a day in fixed brokerage. That is why the trade cap is lower
        here than in the aggressive preset despite holding more positions —
        churn is what makes this configuration lose.
        """
        return cls(
            capital,
            risk_per_trade=0.015,
            daily_loss_limit=0.05,
            max_positions=8,
            max_trades_per_day=20,
            max_portfolio_risk=0.28,
            max_group_risk=0.07,     # tight, so eight positions are really eight bets
            max_leverage=4.0,
        )

    def exposure(self, book: "Book", marks: dict[str, float], session: str | None = None) -> PortfolioExposure:
        """Current risk, with correlated positions collapsed into one exposure."""
        by_group: dict[str, float] = {}
        for symbol, pos in book.positions(session).items():
            if not pos.is_open:
                continue
            mark = marks.get(symbol, pos.average_price)
            at_risk = abs(pos.quantity) * mark * self.stop_loss_pct
            group = correlation_group(symbol)
            by_group[group] = by_group.get(group, 0.0) + at_risk / max(self.capital, 1)

        total = sum(by_group.values())
        largest = max(by_group, key=by_group.get) if by_group else ""
        # Correlated positions are one bet: independence is counted in groups.
        return PortfolioExposure(
            total_risk_pct=round(total, 4),
            by_group={g: round(v, 4) for g, v in by_group.items()},
            effective_bets=float(len(by_group)),
            largest_group=largest,
            largest_group_pct=round(by_group.get(largest, 0.0), 4),
        )

    def gross_exposure(self, book: "Book", marks: dict[str, float],
                       session: str | None = None) -> float:
        """Total notional held, as a multiple of capital.

        Risk-based sizing says nothing about how much stock you are holding: a
        tight stop on an expensive share is a small risk on a large position.
        On a small account that is what actually runs out first.
        """
        notional = 0.0
        for symbol, pos in book.positions(session).items():
            if pos.is_open:
                notional += abs(pos.quantity) * marks.get(symbol, pos.average_price)
        return notional / max(self.capital, 1)

    def consecutive_losses(self, book: "Book", session: str | None = None) -> int:
        """Losing streak length. A long one usually means the regime turned."""
        rows = book.fills(session)
        streak, lots = 0, {}
        results = []
        for row in rows:
            signed = row["quantity"] if row["side"] == BUY else -row["quantity"]
            queue = lots.setdefault(row["symbol"], [])
            if queue and (queue[0][0] > 0) != (signed > 0):
                lot_qty, lot_price = queue.pop(0)
                direction = 1 if lot_qty > 0 else -1
                results.append(direction * (row["price"] - lot_price))
            else:
                queue.append((signed, row["price"]))
        for pnl in reversed(results):
            if pnl < 0:
                streak += 1
            else:
                break
        return streak

    def size(self, price: float) -> int:
        """Position size from risk, not from available margin.

        Quantity is set so that hitting the stop loses ``risk_per_trade`` of
        capital — which is what stops one bad trade from ending the account.
        """
        risk_amount = self.capital * self.risk_per_trade
        risk_per_share = max(price * self.stop_loss_pct, 0.01)
        return max(0, int(risk_amount / risk_per_share))

    def check(
        self,
        book: Book,
        price: float,
        *,
        symbol: str = "",
        marks: dict[str, float] | None = None,
    ) -> RiskDecision:
        session = trading_day()
        if self.halted:
            return RiskDecision(False, f"halted: {self.halt_reason}")

        realized = book.realized_pnl(session)
        loss_limit = -abs(self.capital * self.daily_loss_limit)
        if realized <= loss_limit:
            self.halt(f"daily loss limit hit ({realized:,.0f} vs limit {loss_limit:,.0f})")
            return RiskDecision(False, f"halted: {self.halt_reason}")

        trades = len(book.fills(session))
        if trades >= self.max_trades_per_day:
            return RiskDecision(
                False,
                f"{trades} trades today — over the {self.max_trades_per_day} cap. "
                "SEBI's data puts 80% of traders doing 500+ trades a year in the red; "
                "the cap is what keeps you out of that bucket.",
            )

        # Counted as independent exposures, not raw positions: ten bank stocks
        # are one bet, and the simulation's best configuration was many
        # genuinely uncorrelated positions at small size each.
        exposure_now = self.exposure(book, marks or {}, session)
        if exposure_now.effective_bets >= self.max_positions:
            return RiskDecision(
                False,
                f"{exposure_now.effective_bets:.0f} independent exposures already open "
                f"(cap {self.max_positions})",
            )

        streak = self.consecutive_losses(book, session)
        if streak >= self.max_consecutive_losses:
            self.halt(f"{streak} losses in a row — the regime has probably turned")
            return RiskDecision(False, f"halted: {self.halt_reason}")

        quantity = self.size(price)
        if quantity < 1:
            return RiskDecision(False, f"risk budget gives 0 shares at ₹{price:,.2f}")

        # Margin. Risk sizing decides how much you can afford to lose; this
        # decides how much stock you can actually hold. On a small account the
        # second runs out first, and an order over the line is simply rejected.
        used = self.gross_exposure(book, marks or {}, session)
        headroom = (self.max_leverage - used) * self.capital
        if headroom <= 0:
            return RiskDecision(
                False,
                f"gross exposure already {used:.1f}x capital (cap {self.max_leverage:.1f}x)",
            )
        affordable = int(headroom / max(price, 0.01))
        if affordable < 1:
            return RiskDecision(
                False,
                f"₹{headroom:,.0f} of margin left buys no shares at ₹{price:,.2f} — "
                f"{used:.1f}x of {self.max_leverage:.1f}x already used",
            )

        # Leave room for the other positions. A tight ATR stop asks for an
        # enormous position — risk sizing says 300 shares of a ₹1,000 stock is
        # only ₹1,500 at risk, which is true and still ₹3,00,000 of stock. The
        # first such trade would consume the whole margin and veto the next
        # seven, so a preset that promises eight positions would deliver one.
        per_position = int(self.max_leverage / max(self.max_positions, 1)
                           * self.capital / max(price, 0.01))
        quantity = min(quantity, affordable, max(per_position, 0))
        if quantity < 1:
            return RiskDecision(
                False,
                f"one position's share of margin (₹{self.max_leverage / self.max_positions * self.capital:,.0f}) "
                f"buys no shares at ₹{price:,.2f}",
            )

        # Portfolio limits, with correlated positions counted as one exposure.
        exposure = self.exposure(book, marks or {}, session)
        incoming = quantity * price * self.stop_loss_pct / max(self.capital, 1)

        if exposure.total_risk_pct + incoming > self.max_portfolio_risk:
            return RiskDecision(
                False,
                f"portfolio risk would reach {exposure.total_risk_pct + incoming:.1%}, "
                f"over the {self.max_portfolio_risk:.0%} ceiling — beyond it the median "
                "return falls and ruin rises",
            )

        if symbol:
            group = correlation_group(symbol)
            group_risk = exposure.by_group.get(group, 0.0) + incoming
            if group_risk > self.max_group_risk:
                return RiskDecision(
                    False,
                    f"'{group}' exposure would reach {group_risk:.1%} over the "
                    f"{self.max_group_risk:.0%} cap — these move together, so it is one "
                    "trade taken repeatedly, not diversification",
                )

        return RiskDecision(
            True,
            f"{quantity} shares, stop {self.stop_loss_pct:.1%}, portfolio "
            f"{exposure.total_risk_pct + incoming:.1%} across {exposure.effective_bets:.0f} "
            "independent exposure(s)",
            quantity,
        )

    def halt(self, reason: str) -> None:
        """Kill switch. Nothing new is opened until the engine is restarted."""
        self.halted = True
        self.halt_reason = reason

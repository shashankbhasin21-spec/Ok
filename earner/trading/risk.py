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

from .broker import BUY, SELL, Fill

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
    session    TEXT NOT NULL
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
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def record(self, fill: Fill) -> bool:
        """Store a fill. False if this order id was already recorded."""
        try:
            self.conn.execute(
                "INSERT INTO fills (order_id, symbol, side, quantity, price, paper, at, session)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (fill.order_id, fill.symbol, fill.side, fill.quantity, fill.price,
                 int(fill.paper), fill.at, trading_day(fill.at)),
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

    def realized_pnl(self, session: str | None = None) -> float:
        """Money actually made or lost on *closed* quantity.

        Walks fills in order, matching closes against open lots. An open
        position contributes nothing here no matter how far in front it is.
        """
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
        risk_per_trade: float = 0.01,     # 1% of capital at risk per position
        stop_loss_pct: float = 0.005,     # 0.5% adverse move closes the trade
        daily_loss_limit: float = 0.03,   # 3% down on the day and the engine stops
        max_positions: int = 3,
        max_trades_per_day: int = 15,
    ):
        self.capital = capital
        self.risk_per_trade = risk_per_trade
        self.stop_loss_pct = stop_loss_pct
        self.daily_loss_limit = daily_loss_limit
        self.max_positions = max_positions
        self.max_trades_per_day = max_trades_per_day
        self.halted = False
        self.halt_reason = ""

    def size(self, price: float) -> int:
        """Position size from risk, not from available margin.

        Quantity is set so that hitting the stop loses ``risk_per_trade`` of
        capital — which is what stops one bad trade from ending the account.
        """
        risk_amount = self.capital * self.risk_per_trade
        risk_per_share = max(price * self.stop_loss_pct, 0.01)
        return max(0, int(risk_amount / risk_per_share))

    def check(self, book: Book, price: float, *, marks: dict[str, float] | None = None) -> RiskDecision:
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

        open_positions = sum(1 for p in book.positions(session).values() if p.is_open)
        if open_positions >= self.max_positions:
            return RiskDecision(False, f"{open_positions} positions already open")

        quantity = self.size(price)
        if quantity < 1:
            return RiskDecision(False, f"risk budget gives 0 shares at ₹{price:,.2f}")

        return RiskDecision(True, f"{quantity} shares, stop at {self.stop_loss_pct:.1%}", quantity)

    def halt(self, reason: str) -> None:
        """Kill switch. Nothing new is opened until the engine is restarted."""
        self.halted = True
        self.halt_reason = reason

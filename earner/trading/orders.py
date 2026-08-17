"""Order lifecycle, idempotency and reconciliation.

The gap this closes is the one that separates a paper engine from a live one.
Kotak's ``place_order`` answers with::

    {"stat": "Ok", "nOrdNo": "250122000612876", "stCode": 200}

That is an *acceptance*, not a fill. No price, no filled quantity, no promise
the exchange took it. An engine that treats that response as a fill believes it
owns something it may not own, at a price that may never have traded — and then
sizes its next trade on the fiction.

So the rules here are:

* Intent is written to disk **before** the broker is called. If the process
  dies between the call and the answer, restart finds an order in flight and
  asks the broker what happened, rather than sending it again.
* The broker's answer may only advance an order to ACCEPTED.
* A ``Fill`` is produced by **reconciling against the broker's own order and
  trade books**, never from a quote and never from an acceptance.
* Every order carries a client-generated id that is unique per trading day, so
  the same intent cannot be submitted twice however many times it is retried.

Field names (``nOrdNo``, ``ordSt``, ``avgPrc``, ``fldQty``, ``trnsTp``,
``trdSym``, ``rejRsn``) are Kotak's own, read from the v2 SDK and its response
documentation rather than from memory.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from .broker import BUY, SELL, BrokerError, Fill
from .risk import trading_day

# Local order states.
NEW = "NEW"              # intent written, broker not yet called
ACCEPTED = "ACCEPTED"     # broker returned an order number; working at the exchange
PARTIAL = "PARTIAL"       # some quantity done
FILLED = "FILLED"
REJECTED = "REJECTED"
CANCELLED = "CANCELLED"
UNKNOWN = "UNKNOWN"       # the call failed or the process died mid-flight

TERMINAL = (FILLED, REJECTED, CANCELLED)
IN_FLIGHT = (NEW, ACCEPTED, PARTIAL, UNKNOWN)

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    broker_order_id TEXT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    filled_quantity INTEGER NOT NULL DEFAULT 0,
    average_price   REAL NOT NULL DEFAULT 0,
    order_type      TEXT NOT NULL,
    limit_price     REAL NOT NULL DEFAULT 0,
    state           TEXT NOT NULL,
    intent          TEXT NOT NULL DEFAULT '',
    note            TEXT NOT NULL DEFAULT '',
    session         TEXT NOT NULL,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_by_session ON orders (session, state);
CREATE UNIQUE INDEX IF NOT EXISTS orders_by_broker_id
    ON orders (broker_order_id) WHERE broker_order_id IS NOT NULL;
"""


class DuplicateOrder(RuntimeError):
    """This exact intent has already been submitted today."""


class ReconciliationRequired(RuntimeError):
    """Local state and the broker disagree. Nothing may trade until it is resolved."""


@dataclass
class Order:
    client_order_id: str
    symbol: str
    side: str
    quantity: int
    order_type: str
    limit_price: float = 0.0
    broker_order_id: str = ""
    filled_quantity: int = 0
    average_price: float = 0.0
    state: str = NEW
    intent: str = ""
    note: str = ""
    session: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL

    @property
    def outstanding(self) -> int:
        return max(0, self.quantity - self.filled_quantity)

    def as_fill(self) -> Fill:
        """The fill this order actually produced. Only ever from real numbers."""
        if self.filled_quantity <= 0 or self.average_price <= 0:
            raise BrokerError(
                f"{self.client_order_id} has no confirmed fill "
                f"({self.filled_quantity} @ {self.average_price}) — nothing to book"
            )
        return Fill(
            order_id=self.broker_order_id or self.client_order_id,
            symbol=self.symbol, side=self.side, quantity=self.filled_quantity,
            price=self.average_price, paper=False,
        )


def client_order_id(symbol: str, side: str, quantity: int, intent: str,
                    session: str | None = None) -> str:
    """A deterministic id for one intent on one day.

    Deterministic on purpose: a retry after a timeout regenerates the same id
    and is refused by the primary key, instead of quietly doubling the position.
    """
    session = session or trading_day()
    raw = f"{session}|{symbol.upper()}|{side}|{quantity}|{intent}"
    return f"E{hashlib.sha256(raw.encode()).hexdigest()[:20]}"


# ── the store ───────────────────────────────────────────────────────────────

class OrderStore:
    """Every order this engine has ever intended, and what became of it."""

    def __init__(self, path: Path | str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- write-ahead -------------------------------------------------------

    def open_intent(self, *, symbol: str, side: str, quantity: int, intent: str,
                    order_type: str = "MKT", limit_price: float = 0.0) -> Order:
        """Record what we are about to do, before we do it.

        Raises ``DuplicateOrder`` rather than returning quietly: a caller that
        did not expect a duplicate has a bug, and a caller that did can catch it.
        """
        session = trading_day()
        cid = client_order_id(symbol, side, quantity, intent, session)
        now = time.time()
        try:
            self.conn.execute(
                "INSERT INTO orders (client_order_id, symbol, side, quantity, order_type,"
                " limit_price, state, intent, session, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (cid, symbol.upper(), side, quantity, order_type, limit_price,
                 NEW, intent, session, now, now),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            existing = self.get(cid)
            raise DuplicateOrder(
                f"{symbol} {side} {quantity} for '{intent}' was already submitted today "
                f"as {cid} (state {existing.state if existing else '?'})"
            ) from None
        return Order(client_order_id=cid, symbol=symbol.upper(), side=side,
                     quantity=quantity, order_type=order_type, limit_price=limit_price,
                     state=NEW, intent=intent, session=session)

    def _update(self, cid: str, **fields) -> None:
        fields["updated_at"] = time.time()
        assignments = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(
            f"UPDATE orders SET {assignments} WHERE client_order_id=?",
            (*fields.values(), cid),
        )
        self.conn.commit()

    def mark_accepted(self, cid: str, broker_order_id: str) -> None:
        if not broker_order_id:
            raise ValueError("an accepted order must carry the broker's order id")
        self._update(cid, broker_order_id=str(broker_order_id), state=ACCEPTED)

    def mark_unknown(self, cid: str, note: str) -> None:
        """The broker call failed. We do not know whether the order landed."""
        self._update(cid, state=UNKNOWN, note=note[:500])

    def mark_rejected(self, cid: str, reason: str) -> None:
        self._update(cid, state=REJECTED, note=reason[:500])

    def apply(self, cid: str, *, state: str, filled_quantity: int,
              average_price: float, note: str = "") -> None:
        self._update(cid, state=state, filled_quantity=filled_quantity,
                     average_price=average_price, note=note[:500])

    # -- reads -------------------------------------------------------------

    def _row(self, row: sqlite3.Row) -> Order:
        return Order(
            client_order_id=row["client_order_id"], symbol=row["symbol"], side=row["side"],
            quantity=row["quantity"], order_type=row["order_type"],
            limit_price=row["limit_price"], broker_order_id=row["broker_order_id"] or "",
            filled_quantity=row["filled_quantity"], average_price=row["average_price"],
            state=row["state"], intent=row["intent"], note=row["note"], session=row["session"],
        )

    def get(self, cid: str) -> Order | None:
        row = self.conn.execute(
            "SELECT * FROM orders WHERE client_order_id=?", (cid,)).fetchone()
        return self._row(row) if row else None

    def by_broker_id(self, broker_order_id: str) -> Order | None:
        row = self.conn.execute(
            "SELECT * FROM orders WHERE broker_order_id=?", (str(broker_order_id),)).fetchone()
        return self._row(row) if row else None

    def orders(self, session: str | None = None) -> list[Order]:
        session = session or trading_day()
        return [self._row(r) for r in self.conn.execute(
            "SELECT * FROM orders WHERE session=? ORDER BY created_at", (session,))]

    def in_flight(self, session: str | None = None) -> list[Order]:
        return [o for o in self.orders(session) if o.state in IN_FLIGHT]

    def net_position(self, symbol: str, session: str | None = None) -> int:
        """What the *orders* say we hold. Compared against the broker, not trusted."""
        total = 0
        for order in self.orders(session):
            if order.symbol != symbol.upper() or not order.filled_quantity:
                continue
            total += order.filled_quantity if order.side == BUY else -order.filled_quantity
        return total


# ── translating Kotak's order book ──────────────────────────────────────────

@dataclass
class BrokerOrder:
    """One row of the broker's order book, in our vocabulary."""

    broker_order_id: str
    symbol: str
    side: str
    quantity: int
    filled_quantity: int
    average_price: float
    state: str
    tag: str = ""
    reject_reason: str = ""
    raw: dict = field(default_factory=dict)


# Kotak's ordSt values, lowercased. Anything unrecognised is treated as still
# working rather than as done — assuming "finished" about an unknown state is
# how an engine stops watching an order that is still live.
KOTAK_STATES = {
    "complete": FILLED,
    "traded": FILLED,
    "rejected": REJECTED,
    "cancelled": CANCELLED,
    "canceled": CANCELLED,
}


def normalise_kotak_order(row: dict) -> BrokerOrder:
    def number(key, default=0.0):
        try:
            return float(str(row.get(key, default)).strip() or default)
        except (TypeError, ValueError):
            return default

    status = str(row.get("ordSt") or row.get("stat") or "").strip().lower()
    filled = int(number("fldQty"))
    quantity = int(number("qty"))
    state = KOTAK_STATES.get(status, PARTIAL if filled else ACCEPTED)
    # Kotak reports a completed order as "complete"; trust the quantities over
    # the label when they disagree, because the quantities are what you own.
    if state == FILLED and 0 < filled < quantity:
        state = PARTIAL

    symbol = str(row.get("trdSym") or row.get("sym") or "").upper()
    return BrokerOrder(
        broker_order_id=str(row.get("nOrdNo") or ""),
        symbol=symbol.split("-")[0],
        side=str(row.get("trnsTp") or "").upper(),
        quantity=quantity,
        filled_quantity=filled,
        average_price=number("avgPrc"),
        state=state,
        tag=str(row.get("tag") or row.get("rmk") or ""),
        reject_reason=str(row.get("rejRsn") or "").strip(),
        raw=row,
    )


def read_kotak_position(row: dict) -> tuple[str, int, bool]:
    """One Kotak position row as (symbol, signed quantity, parsed).

    Written against the verified v2 schema rather than from assumption. The
    previous version read ``flBuyQty``, ``flSellQty``, ``buyQty``, ``sellQty``
    and ``quantity`` — **none of which the response contains** — so every real
    position parsed as zero and the divergence check that exists to catch
    "long something you believe you sold" was inert.

    The documented row carries ``sym``/``trdSym``, ``trnsTp`` (B or S),
    ``fldQty`` (filled) and ``qty``. Netted deployments additionally return
    ``flBuyQty``/``flSellQty``; both shapes are handled, and anything else
    returns ``parsed=False`` so the caller raises a divergence instead of
    silently recording a flat position.
    """
    symbol = str(row.get("trdSym") or row.get("sym") or row.get("symbol") or "").upper()
    symbol = symbol.split("-")[0].strip()
    if not symbol:
        return "", 0, False

    def number(*keys) -> float | None:
        for key in keys:
            if key in row and row[key] not in (None, ""):
                try:
                    return float(str(row[key]).strip())
                except (TypeError, ValueError):
                    return None
        return None

    # Netted shape: explicit buy and sell quantities.
    buys, sells = number("flBuyQty", "buyQty"), number("flSellQty", "sellQty")
    if buys is not None or sells is not None:
        return symbol, int((buys or 0) - (sells or 0)), True

    # Per-leg shape: a filled quantity plus a side.
    filled = number("fldQty", "qty", "quantity")
    if filled is None:
        return symbol, 0, False
    side = str(row.get("trnsTp") or "").strip().upper()
    if side not in (BUY, SELL):
        return symbol, 0, False
    return symbol, int(filled if side == BUY else -filled), True


# ── reconciliation ──────────────────────────────────────────────────────────

@dataclass
class Divergence:
    kind: str
    detail: str
    severity: str = "critical"     # critical divergences must halt trading


@dataclass
class ReconcileReport:
    checked: int = 0
    updated: int = 0
    fills: list[Fill] = field(default_factory=list)
    divergences: list[Divergence] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not any(d.severity == "critical" for d in self.divergences)

    def summary(self) -> str:
        if self.clean:
            return (f"{self.checked} order(s) checked, {self.updated} updated, "
                    f"{len(self.fills)} new fill(s) — clean")
        return (f"{self.checked} order(s) checked — "
                f"{len(self.divergences)} divergence(s): "
                + "; ".join(d.detail for d in self.divergences))


class Reconciler:
    """Compares what we believe against what the broker says, and believes the broker.

    Run at start-up, after every order, and before the day is called finished.
    The three things it is looking for, in order of how much they should scare
    you: an order at the broker that we have no record of; an order we think is
    live that the broker has never heard of; and a net position that does not
    match. Any of them means the engine's picture of what it owns is wrong.
    """

    def __init__(self, store: OrderStore, book=None):
        self.store = store
        self.book = book

    def run(self, broker, session: str | None = None) -> ReconcileReport:
        report = ReconcileReport()
        try:
            rows = broker.order_book()
        except BrokerError as exc:
            report.divergences.append(Divergence(
                "broker_unreachable", f"could not read the order book: {exc}"))
            return report

        remote = {r.broker_order_id: r for r in rows if r.broker_order_id}
        local = self.store.orders(session)
        report.checked = len(local)

        for order in local:
            if order.state in TERMINAL:
                continue
            match = remote.get(order.broker_order_id) if order.broker_order_id else None

            if match is None:
                # We think this is live and the broker has never heard of it.
                if order.state == UNKNOWN:
                    # The call failed and nothing landed: safe, and now settled.
                    self.store.mark_rejected(order.client_order_id, "never reached the broker")
                    report.updated += 1
                else:
                    report.divergences.append(Divergence(
                        "missing_at_broker",
                        f"{order.symbol} {order.side} {order.quantity} "
                        f"({order.broker_order_id or 'no broker id'}) is {order.state} locally "
                        "but absent from the broker's order book",
                    ))
                continue

            if match.state != order.state or match.filled_quantity != order.filled_quantity:
                self.store.apply(
                    order.client_order_id, state=match.state,
                    filled_quantity=match.filled_quantity,
                    average_price=match.average_price, note=match.reject_reason,
                )
                report.updated += 1
                if match.filled_quantity > order.filled_quantity and match.average_price > 0:
                    fill = Fill(
                        order_id=f"{match.broker_order_id}:{match.filled_quantity}",
                        symbol=order.symbol, side=order.side,
                        quantity=match.filled_quantity - order.filled_quantity,
                        price=match.average_price, paper=False,
                    )
                    report.fills.append(fill)
                    if self.book is not None:
                        self.book.record(fill)

        # An order at the broker that we never wrote down. Worst case in the file.
        for broker_order_id, row in remote.items():
            if self.store.by_broker_id(broker_order_id) is None and row.state not in TERMINAL:
                report.divergences.append(Divergence(
                    "rogue_order",
                    f"the broker has a live {row.symbol} {row.side} {row.quantity} order "
                    f"({broker_order_id}) that this engine did not place",
                ))

        report.divergences.extend(self.check_positions(broker, session))
        return report

    def check_positions(self, broker, session: str | None = None) -> list[Divergence]:
        """Net quantity per symbol, ours against theirs."""
        try:
            remote_positions = broker.positions()
        except BrokerError as exc:
            return [Divergence("broker_unreachable", f"could not read positions: {exc}")]

        out_unparsed: list[str] = []

        theirs: dict[str, int] = {}
        for row in remote_positions:
            symbol, quantity, ok = read_kotak_position(row)
            if not ok:
                # A row we cannot parse is a divergence, not a zero. Treating an
                # unreadable position as flat is how an engine concludes it owns
                # nothing while the broker holds stock.
                out_unparsed.append(str(row.get("trdSym") or row.get("sym") or row))
                continue
            if symbol:
                theirs[symbol] = theirs.get(symbol, 0) + quantity

        out: list[Divergence] = [
            Divergence("unreadable_position",
                       f"could not read the broker's position row for {name} — "
                       "refusing to assume it is flat")
            for name in out_unparsed
        ]
        symbols = set(theirs) | {o.symbol for o in self.store.orders(session) if o.filled_quantity}
        for symbol in sorted(symbols):
            ours = self.store.net_position(symbol, session)
            their_quantity = theirs.get(symbol, 0)
            if ours != their_quantity:
                out.append(Divergence(
                    "position_mismatch",
                    f"{symbol}: this engine believes {ours:+d}, the broker says "
                    f"{their_quantity:+d}",
                ))
        return out


def require_clean(report: ReconcileReport) -> None:
    """Refuse to continue on a divergence. Called before the engine trades."""
    if not report.clean:
        raise ReconciliationRequired(report.summary())

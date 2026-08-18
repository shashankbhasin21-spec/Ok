"""The live depth subscriber, and the recorder that banks what it sees.

Depth cannot be bought retrospectively. There is no vendor who will sell you
last month's Indian order book at retail prices, which means every session not
recorded is permanently unavailable. That is the whole argument for this
module: it is not primarily a trading component, it is a data-acquisition one.

Run it during market hours and it accumulates the dataset the audit found
missing. After a month there is something to research that did not exist
before; after a quarter there is enough to test a short-horizon hypothesis
honestly.

Safety: this subscribes and records. It places no orders and touches no risk
state. It is read-only against the broker by construction — the class has no
reference to an order path at all.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .depth import OrderBook, parse_depth

# Quiet feeds are indistinguishable from dead ones without a heartbeat.
STALE_AFTER_SECONDS = 30.0


@dataclass
class FeedHealth:
    connected: bool = False
    messages: int = 0
    books: int = 0
    errors: int = 0
    last_message_at: float = 0.0
    last_error: str = ""

    @property
    def stale(self) -> bool:
        if not self.last_message_at:
            return self.connected
        return (time.time() - self.last_message_at) > STALE_AFTER_SECONDS

    def line(self) -> str:
        age = time.time() - self.last_message_at if self.last_message_at else -1
        state = "STALE" if self.stale else ("live" if self.connected else "down")
        return (f"{state}  {self.books:,} books  {self.messages:,} msgs  "
                f"{self.errors} errors  last {age:.0f}s ago")


class DepthFeed:
    """Subscribes to Level-2 depth, keeps the latest book, records every one.

    The SDK delivers messages on its own thread, so the book map is guarded by
    a lock. This is the first genuinely concurrent component in the project and
    the audit flagged the risk in advance: everything else assumed a single
    thread.
    """

    def __init__(self, broker, store=None, *, record: bool = True,
                 workdir: str = ".earner"):
        self.broker = broker
        self.record = record
        self.workdir = Path(workdir)
        self.health = FeedHealth()
        self._books: dict[str, OrderBook] = {}
        self._lock = threading.Lock()
        self._tokens: list[dict] = []
        self.store = store
        if store is None and record:
            from quant_os.ledger.event_store import EventStore

            self.store = EventStore(self.workdir / "depth.db")

    # -- receiving ---------------------------------------------------------

    def on_message(self, message) -> None:
        """Called by the SDK's socket thread for every frame."""
        self.health.messages += 1
        self.health.last_message_at = time.time()

        payloads = message if isinstance(message, list) else [message]
        if isinstance(message, dict) and isinstance(message.get("data"), list):
            payloads = message["data"]

        for payload in payloads:
            try:
                book = parse_depth(payload)
            except Exception as exc:                      # a bad frame is not fatal
                self.health.errors += 1
                self.health.last_error = f"{type(exc).__name__}: {exc}"
                continue
            if book is None:
                continue
            if book.crossed:
                # Bid above ask is never real. Recorded, never traded on.
                self.health.errors += 1
                self.health.last_error = f"{book.symbol}: crossed book"
            with self._lock:
                self._books[book.symbol] = book
            self.health.books += 1
            if self.record and self.store is not None:
                from quant_os.ledger.event_store import MARKET

                self.store.append(MARKET, "depth_feed", "book", **book.features())

    def on_error(self, error) -> None:
        self.health.errors += 1
        self.health.last_error = str(error)[:300]

    def on_open(self, *_args) -> None:
        self.health.connected = True

    def on_close(self, *_args) -> None:
        self.health.connected = False

    # -- subscribing -------------------------------------------------------

    def subscribe(self, symbols: list[str]) -> list[dict]:
        """Resolve symbols to tokens and subscribe to depth.

        Tokens are resolved through the instrument master and never guessed —
        subscribing to a wrong token silently returns another instrument's book,
        which is worse than an error because it looks like data.
        """
        client = self.broker._require()
        client.on_message = self.on_message
        client.on_error = self.on_error
        client.on_open = self.on_open
        client.on_close = self.on_close

        self._tokens = [
            {"instrument_token": self.broker.token_for(symbol),
             "exchange_segment": "nse_cm"}
            for symbol in symbols
        ]
        client.subscribe(instrument_tokens=self._tokens, isDepth=True)
        return self._tokens

    def unsubscribe(self) -> None:
        if self._tokens:
            self.broker._require().un_subscribe(
                instrument_tokens=self._tokens, isDepth=True)
            self._tokens = []

    # -- reading -----------------------------------------------------------

    def book(self, symbol: str) -> OrderBook | None:
        with self._lock:
            return self._books.get(symbol.upper())

    def books(self) -> dict[str, OrderBook]:
        with self._lock:
            return dict(self._books)

    def tradeable(self, symbol: str, *, max_relative_spread: float = 0.002) -> tuple[bool, str]:
        """Whether the book is in a state worth acting on at all.

        This is the NO_TRADE engine's input, and it refuses on conditions the
        OHLCV feed simply could not see: a widened spread, a one-sided book, a
        crossed quote, or a feed that has gone quiet.
        """
        book = self.book(symbol)
        if book is None:
            return False, "no depth received for this symbol"
        if self.health.stale:
            return False, f"feed stale — no message for {STALE_AFTER_SECONDS:.0f}s"
        if book.crossed:
            return False, "crossed book — bid above ask means bad data"
        if not (book.bids and book.asks):
            return False, "one-sided book"
        if book.relative_spread > max_relative_spread:
            return False, (f"spread {book.relative_spread*10000:.0f}bp exceeds "
                           f"{max_relative_spread*10000:.0f}bp")
        return True, "ok"

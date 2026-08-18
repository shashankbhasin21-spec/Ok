"""Level-2 market depth from Kotak's WebSocket, and the book it builds.

This exists because the audit found the gap that blocks every microstructure
feature: the historical feed is OHLCV only — no bid, no ask, no depth, no trade
direction. Those features cannot be approximated from a candle, so until now
they were not built.

Kotak's socket does carry depth, five levels a side, for an authenticated
account. It is not historical — you cannot download last month — which leads to
the point of this module: **start recording now.** Depth is the one input that
cannot be bought retrospectively, so every session not recorded is permanently
lost. Snapshots go to the event store, and after a few months there is a
dataset that did not exist before.

Field names come from the v2 SDK's ``DEPTH_MAPPING`` and ``depth_resp_mapping``,
read from source. One of them is a trap worth stating plainly: the **ask**
quantities are named ``bs``, ``bs1`` … ``bs4`` — not ``sq``. Code written from
the obvious guess silently reads nothing and reports a one-sided book.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

LEVELS = 5


@dataclass(frozen=True)
class Level:
    price: float
    quantity: int
    orders: int = 0


@dataclass
class OrderBook:
    """One depth snapshot, with the microstructure features derived from it."""

    symbol: str
    token: str = ""
    bids: list[Level] = field(default_factory=list)     # best first
    asks: list[Level] = field(default_factory=list)     # best first
    at: float = field(default_factory=time.time)

    # -- the basics --------------------------------------------------------

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0.0

    @property
    def mid(self) -> float:
        if not (self.bids and self.asks):
            return 0.0
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> float:
        if not (self.bids and self.asks):
            return 0.0
        return self.best_ask - self.best_bid

    @property
    def relative_spread(self) -> float:
        """Spread as a fraction of mid. The cost of crossing, before fees."""
        return self.spread / self.mid if self.mid else 0.0

    @property
    def crossed(self) -> bool:
        """Bid above ask. Never real — it means stale or interleaved data."""
        return bool(self.bids and self.asks and self.best_bid > self.best_ask)

    # -- microstructure ----------------------------------------------------

    @property
    def microprice(self) -> float:
        """Size-weighted mid: where the next trade is more likely to print.

        Weighted by the *opposite* side's size, which is the standard
        construction — a large bid pushes the fair price toward the ask
        because it is the side more likely to be consumed last.
        """
        if not (self.bids and self.asks):
            return 0.0
        bid_size, ask_size = self.bids[0].quantity, self.asks[0].quantity
        total = bid_size + ask_size
        if not total:
            return self.mid
        return (self.best_bid * ask_size + self.best_ask * bid_size) / total

    @property
    def imbalance(self) -> float:
        """Top-of-book size imbalance in [-1, 1]. Positive means bid-heavy."""
        if not (self.bids and self.asks):
            return 0.0
        bid_size, ask_size = self.bids[0].quantity, self.asks[0].quantity
        total = bid_size + ask_size
        return (bid_size - ask_size) / total if total else 0.0

    @property
    def depth_imbalance(self) -> float:
        """The same across all levels. Slower and harder to spoof than L1."""
        bid_size = sum(level.quantity for level in self.bids)
        ask_size = sum(level.quantity for level in self.asks)
        total = bid_size + ask_size
        return (bid_size - ask_size) / total if total else 0.0

    @property
    def depth_ratio(self) -> float:
        ask_size = sum(level.quantity for level in self.asks)
        bid_size = sum(level.quantity for level in self.bids)
        return bid_size / ask_size if ask_size else 0.0

    @property
    def concentration(self) -> float:
        """Share of visible size sitting at the touch, in [0, 1].

        High concentration is thin support: the book looks deep in total but
        one aggressive order clears the level everyone is leaning on.
        """
        total = sum(l.quantity for l in self.bids) + sum(l.quantity for l in self.asks)
        if not total:
            return 0.0
        touch = (self.bids[0].quantity if self.bids else 0) + \
                (self.asks[0].quantity if self.asks else 0)
        return touch / total

    def slippage_to_fill(self, quantity: int, side: str = "B") -> float:
        """Volume-weighted price to fill `quantity` by crossing, as a fraction
        of mid. The honest cost of size, rather than an assumed constant.

        Returns ``inf`` when the visible book cannot fill the order — which is
        information, not an error: it means the size is beyond what is shown.
        """
        levels = self.asks if side == "B" else self.bids
        if not levels or not self.mid:
            return float("inf")
        remaining, cost = quantity, 0.0
        for level in levels:
            take = min(remaining, level.quantity)
            cost += take * level.price
            remaining -= take
            if remaining <= 0:
                break
        if remaining > 0:
            return float("inf")
        average = cost / quantity
        return (average - self.mid) / self.mid if side == "B" else (self.mid - average) / self.mid

    def features(self) -> dict:
        """Everything derivable from one snapshot, for the feature store."""
        return {
            "symbol": self.symbol,
            "at": self.at,
            "mid": round(self.mid, 4),
            "microprice": round(self.microprice, 4),
            "spread": round(self.spread, 4),
            "relative_spread": round(self.relative_spread, 6),
            "imbalance": round(self.imbalance, 4),
            "depth_imbalance": round(self.depth_imbalance, 4),
            "depth_ratio": round(self.depth_ratio, 4),
            "concentration": round(self.concentration, 4),
            "bid_size": sum(l.quantity for l in self.bids),
            "ask_size": sum(l.quantity for l in self.asks),
            "crossed": self.crossed,
        }


def _number(value, default=0.0):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return default


def parse_depth(payload: dict) -> OrderBook | None:
    """One SDK depth message into an OrderBook.

    Handles the SDK's already-normalised shape (``depth.buy`` / ``depth.sell``)
    and the raw wire shape, where the **ask sizes are bs/bs1..bs4** rather than
    the sq/sq1.. anyone would guess.
    """
    if not isinstance(payload, dict):
        return None

    symbol = str(payload.get("trading_symbol") or payload.get("ts") or "").upper()
    symbol = symbol.split("-")[0]
    token = str(payload.get("instrument_token") or payload.get("tk") or "")

    depth = payload.get("depth")
    if isinstance(depth, dict):
        bids = [Level(_number(l.get("price")), int(_number(l.get("quantity"))),
                      int(_number(l.get("orders"))))
                for l in (depth.get("buy") or []) if _number(l.get("price")) > 0]
        asks = [Level(_number(l.get("price")), int(_number(l.get("quantity"))),
                      int(_number(l.get("orders"))))
                for l in (depth.get("sell") or []) if _number(l.get("price")) > 0]
    else:
        bid_price = ["bp"] + [f"bp{i}" for i in range(1, LEVELS)]
        bid_size = ["bq"] + [f"bq{i}" for i in range(1, LEVELS)]
        ask_price = ["sp"] + [f"sp{i}" for i in range(1, LEVELS)]
        # Not sq. The SDK names ask sizes bs/bs1..bs4.
        ask_size = ["bs"] + [f"bs{i}" for i in range(1, LEVELS)]
        bids = [Level(_number(payload.get(p)), int(_number(payload.get(q))),
                      int(_number(payload.get(f"bno{i+1}"))))
                for i, (p, q) in enumerate(zip(bid_price, bid_size))
                if _number(payload.get(p)) > 0]
        asks = [Level(_number(payload.get(p)), int(_number(payload.get(q))),
                      int(_number(payload.get(f"sno{i+1}"))))
                for i, (p, q) in enumerate(zip(ask_price, ask_size))
                if _number(payload.get(p)) > 0]

    if not (bids or asks):
        return None
    return OrderBook(symbol=symbol, token=token,
                     bids=sorted(bids, key=lambda l: -l.price),
                     asks=sorted(asks, key=lambda l: l.price))

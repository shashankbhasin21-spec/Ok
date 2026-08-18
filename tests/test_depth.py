"""Order book parsing, microstructure features, and the depth feed.

The parsing tests matter most. The last time this project wrote a broker
parser from assumption rather than from the schema, every position read as
zero and the reconciliation safety net was silently inert. These tests use the
SDK's own documented shapes.
"""

from __future__ import annotations

import pytest

from quant_os.data.depth import Level, OrderBook, parse_depth
from quant_os.data.depth_feed import DepthFeed


def book(bids, asks, symbol="RELIANCE") -> OrderBook:
    return OrderBook(symbol=symbol,
                     bids=[Level(p, q) for p, q in bids],
                     asks=[Level(p, q) for p, q in asks])


# ── parsing the SDK's shapes ────────────────────────────────────────────────

SDK_SHAPE = {
    "instrument_token": "2885", "trading_symbol": "RELIANCE-EQ",
    "exchange_segment": "nse_cm",
    "depth": {
        "buy": [{"price": 1400.0, "quantity": 500, "orders": 12},
                {"price": 1399.9, "quantity": 800, "orders": 20}],
        "sell": [{"price": 1400.1, "quantity": 150, "orders": 4},
                 {"price": 1400.2, "quantity": 300, "orders": 9}],
    },
}


def test_the_sdks_normalised_shape_parses():
    b = parse_depth(SDK_SHAPE)
    assert b is not None
    assert b.symbol == "RELIANCE" and b.token == "2885"
    assert b.best_bid == 1400.0 and b.best_ask == 1400.1
    assert len(b.bids) == 2 and len(b.asks) == 2


def test_the_raw_wire_shape_parses_with_the_right_ask_field():
    """The trap: ask sizes are bs/bs1..bs4 in DEPTH_MAPPING, NOT sq. Reading
    sq returns nothing and produces a one-sided book that looks like real data."""
    raw = {"ts": "TCS-EQ", "tk": "11536",
           "bp": 3400.0, "bp1": 3399.5, "bq": 200, "bq1": 400,
           "sp": 3400.5, "sp1": 3401.0, "bs": 150, "bs1": 350,
           "bno1": 5, "bno2": 8, "sno1": 3, "sno2": 6}
    b = parse_depth(raw)
    assert b is not None
    assert b.symbol == "TCS"
    assert [l.quantity for l in b.asks] == [150, 350], "ask sizes come from bs/bs1"
    assert [l.quantity for l in b.bids] == [200, 400]


def test_levels_are_ordered_best_first_regardless_of_input_order():
    raw = {"ts": "X", "bp": 99.0, "bp1": 100.0, "bq": 10, "bq1": 20,
           "sp": 102.0, "sp1": 101.0, "bs": 10, "bs1": 20}
    b = parse_depth(raw)
    assert b.best_bid == 100.0, "highest bid is best"
    assert b.best_ask == 101.0, "lowest ask is best"


def test_empty_and_malformed_payloads_return_none():
    assert parse_depth({}) is None
    assert parse_depth({"ts": "X"}) is None
    assert parse_depth("not a dict") is None


def test_zero_priced_levels_are_dropped_not_kept_as_zero():
    """Unfilled book levels arrive as 0.0 and would corrupt every average."""
    raw = {"ts": "X", "bp": 100.0, "bp1": 0.0, "bq": 10, "bq1": 0,
           "sp": 101.0, "sp1": 0.0, "bs": 10, "bs1": 0}
    b = parse_depth(raw)
    assert len(b.bids) == 1 and len(b.asks) == 1


# ── microstructure ──────────────────────────────────────────────────────────

def test_spread_and_mid():
    b = book([(100.0, 10)], [(100.5, 10)])
    assert b.spread == pytest.approx(0.5)
    assert b.mid == pytest.approx(100.25)
    assert b.relative_spread == pytest.approx(0.5 / 100.25)


def test_microprice_leans_toward_the_thin_side():
    """A large bid and a small ask means the ask gets consumed first, so fair
    value sits above the mid."""
    b = book([(100.0, 1000)], [(100.5, 100)])
    assert b.microprice > b.mid
    thin_bid = book([(100.0, 100)], [(100.5, 1000)])
    assert thin_bid.microprice < thin_bid.mid


def test_a_balanced_book_prices_at_the_mid():
    b = book([(100.0, 500)], [(100.5, 500)])
    assert b.microprice == pytest.approx(b.mid)
    assert b.imbalance == pytest.approx(0.0)


def test_imbalance_is_bounded_and_signed():
    assert book([(100.0, 900)], [(101.0, 100)]).imbalance == pytest.approx(0.8)
    assert book([(100.0, 100)], [(101.0, 900)]).imbalance == pytest.approx(-0.8)


def test_depth_imbalance_uses_the_whole_book():
    """L1 bid-heavy but the full book ask-heavy — the two must disagree, which
    is the point of computing both."""
    b = book([(100.0, 500), (99.9, 100)], [(100.5, 100), (100.6, 2000)])
    assert b.imbalance > 0
    assert b.depth_imbalance < 0


def test_concentration_detects_a_book_leaning_on_the_touch():
    thin = book([(100.0, 900), (99.9, 50)], [(100.5, 900), (100.6, 50)])
    deep = book([(100.0, 100), (99.9, 900)], [(100.5, 100), (100.6, 900)])
    assert thin.concentration > deep.concentration


def test_a_crossed_book_is_flagged_rather_than_used():
    assert book([(101.0, 10)], [(100.0, 10)]).crossed is True
    assert book([(100.0, 10)], [(101.0, 10)]).crossed is False


def test_an_empty_book_returns_zeros_not_exceptions():
    empty = OrderBook(symbol="X")
    assert empty.mid == 0.0 and empty.microprice == 0.0
    assert empty.imbalance == 0.0 and empty.concentration == 0.0


# ── the cost of size ────────────────────────────────────────────────────────

def test_slippage_grows_with_size():
    b = book([(99.5, 100), (99.0, 500)], [(100.5, 100), (101.0, 500)])
    small = b.slippage_to_fill(50, "B")
    large = b.slippage_to_fill(600, "B")
    assert 0 < small < large


def test_size_beyond_the_visible_book_is_infinite_not_optimistic():
    """Returning a finite number here is how a backtest assumes liquidity that
    was never shown."""
    b = book([(99.5, 100)], [(100.5, 100)])
    assert b.slippage_to_fill(10_000, "B") == float("inf")


def test_selling_slippage_is_measured_the_other_way():
    b = book([(99.5, 100), (99.0, 500)], [(100.5, 100), (101.0, 500)])
    assert b.slippage_to_fill(50, "S") > 0


# ── the feed ────────────────────────────────────────────────────────────────

class FakeBroker:
    def __init__(self):
        self.client = self
        self.subscribed = None
        self.on_message = self.on_error = self.on_open = self.on_close = None

    def _require(self):
        return self

    def token_for(self, symbol):
        return {"RELIANCE": "2885", "TCS": "11536"}[symbol]

    def subscribe(self, instrument_tokens, isDepth=False, isIndex=False):
        self.subscribed = (instrument_tokens, isDepth)


def test_the_feed_resolves_tokens_and_asks_for_depth(tmp_path):
    feed = DepthFeed(FakeBroker(), record=False, workdir=str(tmp_path))
    tokens = feed.subscribe(["RELIANCE", "TCS"])
    assert [t["instrument_token"] for t in tokens] == ["2885", "11536"]
    assert feed.broker.subscribed[1] is True, "must request depth, not just LTP"


def test_an_unknown_symbol_raises_rather_than_subscribing_to_a_guess(tmp_path):
    feed = DepthFeed(FakeBroker(), record=False, workdir=str(tmp_path))
    with pytest.raises(KeyError):
        feed.subscribe(["NOTLISTED"])


def test_messages_become_books(tmp_path):
    feed = DepthFeed(FakeBroker(), record=False, workdir=str(tmp_path))
    feed.on_message([SDK_SHAPE])
    b = feed.book("RELIANCE")
    assert b is not None and b.best_bid == 1400.0
    assert feed.health.books == 1


def test_a_bad_frame_does_not_kill_the_feed(tmp_path):
    feed = DepthFeed(FakeBroker(), record=False, workdir=str(tmp_path))
    feed.on_message(["garbage", None, SDK_SHAPE])
    assert feed.book("RELIANCE") is not None, "the good frame still landed"


def test_snapshots_are_recorded_when_asked(tmp_path):
    feed = DepthFeed(FakeBroker(), record=True, workdir=str(tmp_path))
    feed.on_message([SDK_SHAPE])
    events = feed.store.replay()
    assert len(events) == 1 and events[0].payload["symbol"] == "RELIANCE"
    feed.store.close()


# ── the no-trade conditions depth makes visible ─────────────────────────────

def test_a_wide_spread_blocks_trading(tmp_path):
    feed = DepthFeed(FakeBroker(), record=False, workdir=str(tmp_path))
    feed.on_message([{"ts": "X-EQ", "bp": 100.0, "bq": 10,
                      "sp": 102.0, "bs": 10}])
    ok, why = feed.tradeable("X", max_relative_spread=0.002)
    assert not ok and "spread" in why


def test_a_one_sided_book_blocks_trading(tmp_path):
    feed = DepthFeed(FakeBroker(), record=False, workdir=str(tmp_path))
    feed.on_message([{"ts": "X-EQ", "bp": 100.0, "bq": 10}])
    ok, why = feed.tradeable("X")
    assert not ok and "one-sided" in why


def test_an_unseen_symbol_blocks_trading(tmp_path):
    feed = DepthFeed(FakeBroker(), record=False, workdir=str(tmp_path))
    ok, why = feed.tradeable("RELIANCE")
    assert not ok and "no depth" in why


def test_a_healthy_tight_book_is_tradeable(tmp_path):
    feed = DepthFeed(FakeBroker(), record=False, workdir=str(tmp_path))
    feed.on_message([SDK_SHAPE])
    ok, why = feed.tradeable("RELIANCE")
    assert ok, why

"""Order lifecycle, idempotency and reconciliation.

These are the tests that decide whether this may ever touch real money. The
question each one asks is the same: after this failure, does the engine still
know what it owns?
"""

from __future__ import annotations

import pytest

from earner.trading.broker import BUY, SELL, BrokerError, PaperBroker
from earner.trading.orders import (
    ACCEPTED, CANCELLED, FILLED, NEW, PARTIAL, REJECTED,
    BrokerOrder, DuplicateOrder, OrderStore, ReconciliationRequired, Reconciler,
    client_order_id, normalise_kotak_order, require_clean,
)
from earner.trading.risk import Book


@pytest.fixture
def store(tmp_path):
    s = OrderStore(tmp_path / "orders.db")
    yield s
    s.close()


class FakeBroker:
    """Answers with whatever order book and positions you hand it."""

    live = True

    def __init__(self, orders=None, positions=None, fail: str = ""):
        self._orders = list(orders or [])
        self._positions = list(positions or [])
        self.fail = fail

    def order_book(self):
        if self.fail:
            raise BrokerError(self.fail)
        return list(self._orders)

    def positions(self):
        if self.fail:
            raise BrokerError(self.fail)
        return list(self._positions)


def remote(order_id, symbol="RELIANCE", side=BUY, quantity=10, filled=10,
           price=100.0, state=FILLED, **kw) -> BrokerOrder:
    return BrokerOrder(broker_order_id=order_id, symbol=symbol, side=side,
                       quantity=quantity, filled_quantity=filled,
                       average_price=price, state=state, **kw)


# ── idempotency ─────────────────────────────────────────────────────────────

def test_the_same_intent_twice_is_refused(store):
    """A retry after a timeout must not double the position."""
    store.open_intent(symbol="RELIANCE", side=BUY, quantity=10, intent="orb-entry")
    with pytest.raises(DuplicateOrder, match="already submitted today"):
        store.open_intent(symbol="RELIANCE", side=BUY, quantity=10, intent="orb-entry")


def test_a_different_intent_on_the_same_symbol_is_allowed(store):
    store.open_intent(symbol="RELIANCE", side=BUY, quantity=10, intent="orb-entry")
    store.open_intent(symbol="RELIANCE", side=SELL, quantity=10, intent="orb-exit")
    assert len(store.orders()) == 2


def test_the_client_order_id_is_deterministic_across_processes():
    """It has to be: a restarted process regenerates it and hits the primary key."""
    a = client_order_id("RELIANCE", BUY, 10, "orb-entry", session="2026-08-18")
    b = client_order_id("RELIANCE", BUY, 10, "orb-entry", session="2026-08-18")
    assert a == b
    assert a != client_order_id("RELIANCE", BUY, 11, "orb-entry", session="2026-08-18")
    assert a != client_order_id("RELIANCE", BUY, 10, "orb-entry", session="2026-08-19")


def test_intent_is_written_before_the_broker_is_called(store):
    """The write-ahead. If the process dies now, restart finds this."""
    order = store.open_intent(symbol="TCS", side=BUY, quantity=5, intent="vwap")
    assert store.get(order.client_order_id).state == NEW
    assert store.in_flight() == [store.get(order.client_order_id)]


# ── an acceptance is not a fill ─────────────────────────────────────────────

def test_an_accepted_order_has_no_fill_to_book(store):
    """Kotak's place_order returns an order number and nothing else."""
    order = store.open_intent(symbol="TCS", side=BUY, quantity=5, intent="vwap")
    store.mark_accepted(order.client_order_id, "250122000612876")

    accepted = store.get(order.client_order_id)
    assert accepted.state == ACCEPTED
    with pytest.raises(BrokerError, match="no confirmed fill"):
        accepted.as_fill()


def test_an_accepted_order_must_carry_the_brokers_id(store):
    order = store.open_intent(symbol="TCS", side=BUY, quantity=5, intent="vwap")
    with pytest.raises(ValueError):
        store.mark_accepted(order.client_order_id, "")


def test_a_confirmed_fill_uses_the_exchange_price(store):
    order = store.open_intent(symbol="TCS", side=BUY, quantity=5, intent="vwap")
    store.mark_accepted(order.client_order_id, "250122000612876")
    store.apply(order.client_order_id, state=FILLED, filled_quantity=5, average_price=3_412.55)

    fill = store.get(order.client_order_id).as_fill()
    assert fill.price == 3_412.55 and fill.quantity == 5
    assert fill.paper is False


# ── translating Kotak's order book ──────────────────────────────────────────

def test_kotak_rows_are_translated_faithfully():
    row = {"nOrdNo": "250122000612876", "ordSt": "complete", "avgPrc": "9.39",
           "fldQty": 1, "qty": 1, "trnsTp": "B", "trdSym": "IDEA-EQ", "sym": "IDEA"}
    order = normalise_kotak_order(row)
    assert order.broker_order_id == "250122000612876"
    assert order.symbol == "IDEA" and order.side == BUY
    assert order.state == FILLED and order.average_price == 9.39


def test_a_partial_fill_labelled_complete_is_treated_as_partial():
    """Believe the quantities over the label — the quantities are what you own."""
    row = {"nOrdNo": "1", "ordSt": "complete", "avgPrc": "100", "fldQty": 4, "qty": 10,
           "trnsTp": "B", "trdSym": "TCS-EQ"}
    assert normalise_kotak_order(row).state == PARTIAL


def test_an_unrecognised_status_is_treated_as_still_working():
    """Assuming 'done' about a state you do not know is how you stop watching a
    live order."""
    row = {"nOrdNo": "1", "ordSt": "trigger pending", "avgPrc": "0", "fldQty": 0,
           "qty": 10, "trnsTp": "B", "trdSym": "TCS-EQ"}
    assert normalise_kotak_order(row).state == ACCEPTED


def test_rejections_keep_their_reason():
    row = {"nOrdNo": "1", "ordSt": "rejected", "qty": 10, "fldQty": 0, "trnsTp": "B",
           "trdSym": "TCS-EQ", "rejRsn": "insufficient margin"}
    order = normalise_kotak_order(row)
    assert order.state == REJECTED and order.reject_reason == "insufficient margin"


def test_a_malformed_row_does_not_crash_the_reconciler():
    order = normalise_kotak_order({"nOrdNo": "1", "avgPrc": "", "fldQty": None, "qty": "x"})
    assert order.average_price == 0.0 and order.quantity == 0


# ── reconciliation ──────────────────────────────────────────────────────────

def test_reconciliation_learns_the_real_fill_from_the_broker(store, tmp_path):
    book = Book(tmp_path / "book.db")
    order = store.open_intent(symbol="RELIANCE", side=BUY, quantity=10, intent="orb")
    store.mark_accepted(order.client_order_id, "OID-1")

    broker = FakeBroker(
        orders=[remote("OID-1", price=1_402.75)],
        positions=[{"trdSym": "RELIANCE-EQ", "flBuyQty": 10, "flSellQty": 0}],
    )
    report = Reconciler(store, book).run(broker)

    assert report.clean and report.updated == 1
    assert report.fills[0].price == 1_402.75
    assert store.get(order.client_order_id).state == FILLED
    assert book.realized_pnl() == 0.0 and len(book.fills()) == 1
    book.close()


def test_only_the_newly_filled_quantity_is_booked(store):
    """Reconciling twice must not book the same shares twice."""
    order = store.open_intent(symbol="RELIANCE", side=BUY, quantity=10, intent="orb")
    store.mark_accepted(order.client_order_id, "OID-1")
    broker = FakeBroker(orders=[remote("OID-1", filled=4, state=PARTIAL)],
                        positions=[{"trdSym": "RELIANCE", "flBuyQty": 4, "flSellQty": 0}])

    first = Reconciler(store).run(broker)
    assert first.fills[0].quantity == 4

    broker._orders = [remote("OID-1", filled=10, state=FILLED)]
    broker._positions = [{"trdSym": "RELIANCE", "flBuyQty": 10, "flSellQty": 0}]
    second = Reconciler(store).run(broker)
    assert second.fills[0].quantity == 6, "only the six new shares"


def test_an_order_the_engine_never_placed_is_a_critical_divergence(store):
    """The worst case in the file: the broker is holding something we did not do."""
    broker = FakeBroker(orders=[remote("NOT-OURS", state=ACCEPTED, filled=0)],
                        positions=[])
    report = Reconciler(store).run(broker)

    assert not report.clean
    assert any(d.kind == "rogue_order" for d in report.divergences)
    with pytest.raises(ReconciliationRequired):
        require_clean(report)


def test_an_order_we_think_is_live_but_the_broker_never_saw(store):
    order = store.open_intent(symbol="TCS", side=BUY, quantity=5, intent="vwap")
    store.mark_accepted(order.client_order_id, "OID-GHOST")

    report = Reconciler(store).run(FakeBroker(orders=[], positions=[]))
    assert not report.clean
    assert any(d.kind == "missing_at_broker" for d in report.divergences)


def test_a_failed_submission_that_never_landed_is_settled_not_alarming(store):
    """The process died mid-call. The broker has nothing, so nothing happened —
    that is recoverable, and must not be confused with a lost order."""
    order = store.open_intent(symbol="TCS", side=BUY, quantity=5, intent="vwap")
    store.mark_unknown(order.client_order_id, "connection reset")

    report = Reconciler(store).run(FakeBroker(orders=[], positions=[]))
    assert report.clean, "an order that never reached the broker is not a divergence"
    assert store.get(order.client_order_id).state == REJECTED


def test_a_position_the_engine_does_not_agree_with_stops_everything(store):
    """The failure the whole module exists to prevent: being long something you
    believe you already sold."""
    order = store.open_intent(symbol="RELIANCE", side=BUY, quantity=10, intent="orb")
    store.mark_accepted(order.client_order_id, "OID-1")
    store.apply(order.client_order_id, state=FILLED, filled_quantity=10, average_price=100.0)

    broker = FakeBroker(orders=[remote("OID-1")],
                        positions=[{"trdSym": "RELIANCE-EQ", "flBuyQty": 25, "flSellQty": 0}])
    report = Reconciler(store).run(broker)

    assert not report.clean
    mismatch = next(d for d in report.divergences if d.kind == "position_mismatch")
    assert "believes +10" in mismatch.detail and "broker says +25" in mismatch.detail


def test_a_flat_symbol_matches_a_broker_that_has_no_position(store):
    order = store.open_intent(symbol="RELIANCE", side=BUY, quantity=10, intent="in")
    store.mark_accepted(order.client_order_id, "OID-1")
    store.apply(order.client_order_id, state=FILLED, filled_quantity=10, average_price=100.0)
    exit_order = store.open_intent(symbol="RELIANCE", side=SELL, quantity=10, intent="out")
    store.mark_accepted(exit_order.client_order_id, "OID-2")
    store.apply(exit_order.client_order_id, state=FILLED, filled_quantity=10, average_price=101.0)

    report = Reconciler(store).run(FakeBroker(
        orders=[remote("OID-1"), remote("OID-2", side=SELL, price=101.0)], positions=[]))
    assert report.clean
    assert store.net_position("RELIANCE") == 0


def test_an_unreachable_broker_is_a_divergence_not_a_clean_bill(store):
    """Silence is not agreement."""
    report = Reconciler(store).run(FakeBroker(fail="socket timeout"))
    assert not report.clean
    assert any(d.kind == "broker_unreachable" for d in report.divergences)


def test_a_cancelled_order_needs_no_further_attention(store):
    order = store.open_intent(symbol="TCS", side=BUY, quantity=5, intent="vwap")
    store.mark_accepted(order.client_order_id, "OID-1")
    store.apply(order.client_order_id, state=CANCELLED, filled_quantity=0, average_price=0.0)

    report = Reconciler(store).run(FakeBroker(orders=[], positions=[]))
    assert report.clean, "a terminal order absent from the book is finished, not missing"


# ── the paper broker exposes the same surface ───────────────────────────────

def test_paper_orders_reconcile_through_the_same_path(cfg, tmp_path):
    """Whatever is proven on paper runs the identical reconciliation live."""
    store = OrderStore(tmp_path / "orders.db")
    broker = PaperBroker(cfg, starting_capital=100_000.0)
    broker.set_price("RELIANCE", 1_400.0)

    order = store.open_intent(symbol="RELIANCE", side=BUY, quantity=10, intent="orb")
    broker_id = broker.submit(symbol="RELIANCE", token="1", side=BUY, quantity=10)
    store.mark_accepted(order.client_order_id, broker_id)

    report = Reconciler(store).run(broker)
    assert report.clean
    assert store.get(order.client_order_id).state == FILLED
    assert report.fills[0].quantity == 10
    store.close()

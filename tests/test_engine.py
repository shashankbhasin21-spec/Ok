"""The engine loop: gate ordering, EV, risk veto, and position management.

The load-bearing tests are the ones about *ordering*. A signal is a proposal;
only the risk engine may permit. And exits must run on every tick regardless of
what signal generation did — a strategy failing must never leave real money in
an unmanaged position.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from earner.trading.broker import BUY, SELL, BrokerError, Fill
from earner.trading.engine import Engine, ManagedPosition
from earner.trading.risk import Book, RiskManager
from earner.trading.session import load_session
from earner.trading.strategy import Signal, Strategy

IST_TZ = timezone(timedelta(hours=5, minutes=30))
ALL_REGIMES = ("STRONG_BULL", "BULL", "RANGE", "NEUTRAL", "BEAR", "STRONG_BEAR",
               "EXTREME_VOLATILITY")

OPEN = datetime(2026, 8, 18, 10, 30, tzinfo=IST_TZ)        # Tuesday, mid-session
NEAR_CLOSE = datetime(2026, 8, 18, 15, 20, tzinfo=IST_TZ)  # square-off window
AFTER_HOURS = datetime(2026, 8, 18, 17, 0, tzinfo=IST_TZ)


class StubBroker:
    """Fills at a price you set. No costs — the arithmetic stays readable."""

    live = False

    def __init__(self, price: float = 100.0):
        self.price = price
        self.orders: list[dict] = []
        self.fail_next = False

    def place(self, *, symbol, token, side, quantity, order_type="MKT", price=0.0):
        if self.fail_next:
            raise BrokerError("exchange rejected the order")
        self.orders.append({"symbol": symbol, "side": side, "quantity": quantity})
        return Fill(order_id=f"stub-{len(self.orders)}", symbol=symbol, side=side,
                    quantity=quantity, price=price or self.price)


class Scripted(Strategy):
    """Returns the same signal every time, in every regime."""

    name = "scripted"
    regimes = ALL_REGIMES

    def __init__(self, signal: Signal):
        self.signal = signal

    def evaluate(self, symbol, candles):
        return replace(self.signal, symbol=symbol)


class Broken(Strategy):
    name = "broken"
    regimes = ALL_REGIMES

    def evaluate(self, symbol, candles):
        raise ZeroDivisionError("indicator blew up")


def signal(side=BUY, entry=100.0, stop=99.0, target=103.0, confidence=0.6) -> Signal:
    return Signal(symbol="RELIANCE", side=side, strategy="scripted", confidence=confidence,
                  entry=entry, stop=stop, target=target,
                  thesis="scripted for the test", invalidation="loss of the stop")


def flat_candles(n: int = 60, price: float = 100.0):
    from tests.test_strategy import series
    return series([price] * n)


@pytest.fixture
def book(tmp_path):
    b = Book(tmp_path / "engine.db")
    yield b
    b.close()


def build(book, strategies, *, capital=100_000.0, price=100.0, orders=None, **risk_kwargs):
    broker = StubBroker(price)
    risk = RiskManager(capital=capital, **risk_kwargs)
    engine = Engine(broker, book, risk, session=load_session({"TRADING_MODE": "PAPER"}),
                    strategies=strategies, orders=orders)
    return engine, broker, risk


def stages(engine) -> list[str]:
    return [e.stage for e in engine.events]


# ── the gates, in order ─────────────────────────────────────────────────────

def test_a_positive_edge_becomes_a_position(book):
    engine, broker, _ = build(book, [Scripted(signal())])
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    assert len(broker.orders) == 1
    assert "RELIANCE" in engine.positions
    assert engine.positions["RELIANCE"].stop == 99.0, "the exit plan travels with the position"


def test_a_negative_expected_value_is_never_traded(book):
    """3:1 against at even odds. The story does not matter; the arithmetic does."""
    engine, broker, _ = build(book, [Scripted(signal(stop=97.0, target=101.0, confidence=0.5))])
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    assert broker.orders == []
    assert "ev_gate" in stages(engine)


def test_the_risk_engine_can_veto_a_signal_the_strategy_liked(book):
    """Strategies propose; only risk permits (spec §5)."""
    engine, broker, _ = build(book, [Scripted(signal())], max_trades_per_day=0)
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    assert broker.orders == []
    assert "risk_veto" in stages(engine)
    assert "signal" in stages(engine), "the veto happens after the signal, not instead of it"


def test_a_halted_engine_opens_nothing(book):
    engine, broker, risk = build(book, [Scripted(signal())])
    risk.halt("manual kill switch")
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    assert broker.orders == []
    assert "signal" not in stages(engine), "a halted engine should not even evaluate"


def test_extreme_volatility_stands_aside(book):
    from tests.test_strategy import series, zigzag

    engine, broker, _ = build(book, [Scripted(signal())])
    engine.tick({"RELIANCE": series(zigzag(100.0, 5.0, 5.0, 60))}, now=OPEN)

    assert broker.orders == []
    assert "regime" in stages(engine)


def test_size_is_capped_by_the_signals_own_stop(book):
    """A wide stop buys fewer shares. Risk per trade is the constant, not size."""
    engine, broker, _ = build(book, [Scripted(signal(stop=95.0, target=115.0))],
                              capital=100_000.0)
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    # 1% of 100,000 = 1,000 of risk, over a 5.00 stop = 200 shares.
    assert broker.orders[0]["quantity"] == 200


def test_a_signal_priced_at_a_level_the_market_has_left_is_discarded(book):
    """The stop distance is what sizing is built on. If the market has moved a
    long way from the price the signal was worked out at, that geometry is
    fiction and the trade being approved is not the trade you would get."""
    engine, broker, _ = build(book, [Scripted(signal(entry=100.0, stop=99.0))], price=104.0)
    engine.tick({"RELIANCE": flat_candles(price=104.0)}, now=OPEN)

    assert broker.orders == []
    assert "stale_signal" in stages(engine)


def test_a_signal_with_no_stop_distance_is_discarded(book):
    engine, broker, _ = build(book, [Scripted(signal(stop=100.0))])
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    assert broker.orders == []
    assert "malformed" in stages(engine)


def test_a_symbol_is_not_re_entered_on_the_bar_it_exited(book):
    """Otherwise an exit and a still-valid signal churn against each other,
    paying brokerage both ways for a position that never changes."""
    engine, broker, _ = build(book, [Scripted(signal(stop=98.0, target=100.9, confidence=0.8))])
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)
    assert len(broker.orders) == 1

    broker.price = 100.95                                 # through the target
    engine.tick({"RELIANCE": flat_candles(price=100.95)}, now=OPEN)

    assert engine.positions == {}
    assert len(broker.orders) == 2, "exited once, did not immediately re-open"
    assert "RELIANCE" in engine.just_exited

    # A cooldown of one bar, not a ban: the setup is tradeable again next tick.
    engine.tick({"RELIANCE": flat_candles(price=100.95)}, now=OPEN)
    assert len(broker.orders) == 3


def test_the_same_symbol_is_not_stacked(book):
    engine, broker, _ = build(book, [Scripted(signal())])
    candles = flat_candles()
    engine.tick({"RELIANCE": candles}, now=OPEN)
    engine.tick({"RELIANCE": candles}, now=OPEN)

    assert len(broker.orders) == 1


# ── position management is independent of signal generation (spec §18) ──────

def test_exits_run_even_when_the_market_stops_accepting_orders(book):
    """The single most important ordering in the file."""
    engine, broker, _ = build(book, [Scripted(signal())])
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)
    assert "RELIANCE" in engine.positions

    broker.price = 98.0                                   # gapped through the stop
    engine.tick({"RELIANCE": flat_candles(price=98.0)}, now=AFTER_HOURS)

    assert engine.positions == {}, "a closed market must not strand a live position"
    assert broker.orders[-1]["side"] == SELL


def test_a_broken_strategy_does_not_stop_the_desk(book):
    engine, broker, _ = build(book, [Broken(), Scripted(signal())])
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    assert "strategy_error" in stages(engine)
    assert len(broker.orders) == 1, "the working strategy still traded"


def test_a_broken_strategy_does_not_strand_an_open_position(book):
    engine, broker, _ = build(book, [Scripted(signal())])
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    engine.strategies = [Broken()]
    broker.price = 98.5
    engine.tick({"RELIANCE": flat_candles(price=98.5)}, now=OPEN)

    assert engine.positions == {}, "the stop must fire whatever the strategies did"


def test_square_off_flattens_before_the_mis_cutoff(book):
    engine, broker, _ = build(book, [Scripted(signal())])
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    engine.tick({"RELIANCE": flat_candles()}, now=NEAR_CLOSE)

    assert engine.positions == {}
    assert any("square_off" in e.detail for e in engine.events)


def test_square_off_does_not_need_a_fresh_mark(book):
    """A dropped tick must not carry a position past the cutoff into delivery."""
    engine, broker, _ = build(book, [Scripted(signal())])
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    engine.tick({}, now=NEAR_CLOSE)                       # no prices at all

    assert engine.positions == {}
    assert broker.orders[-1]["side"] == SELL


def test_a_failed_exit_keeps_the_position_and_says_so_loudly(book):
    engine, broker, _ = build(book, [Scripted(signal())])
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    broker.fail_next = True
    broker.price = 98.0
    engine.tick({"RELIANCE": flat_candles(price=98.0)}, now=OPEN)

    assert "RELIANCE" in engine.positions, "a rejected exit leaves you still exposed"
    assert any("POSITION STILL OPEN" in e.detail for e in engine.events)


# ── the exit plan ───────────────────────────────────────────────────────────

def test_a_long_exits_at_its_stop_and_its_target():
    position = ManagedPosition("X", BUY, 10, entry=100.0, stop=99.0, target=103.0,
                               strategy="t", thesis="")
    assert position.exit_reason(99.0) == "stop"
    assert position.exit_reason(103.5) == "target"
    assert position.exit_reason(101.0) is None
    assert position.unrealized(101.0) == 10.0


def test_a_short_exits_the_other_way_round():
    position = ManagedPosition("X", SELL, 10, entry=100.0, stop=101.0, target=97.0,
                               strategy="t", thesis="")
    assert position.exit_reason(101.5) == "stop"
    assert position.exit_reason(96.0) == "target"
    assert position.exit_reason(99.0) is None
    assert position.unrealized(98.0) == 20.0


def test_a_short_signal_sells_to_open_and_buys_to_close(book):
    engine, broker, _ = build(book, [Scripted(signal(side=SELL, stop=101.0, target=97.0))])
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)
    assert broker.orders[0]["side"] == SELL

    broker.price = 96.0
    engine.tick({"RELIANCE": flat_candles(price=96.0)}, now=OPEN)
    assert broker.orders[-1]["side"] == BUY
    assert engine.positions == {}


# ── controls and reporting ──────────────────────────────────────────────────

def test_the_daily_stop_measures_open_positions_not_just_closed_ones(book):
    """Waiting for the next signal to notice the loss is how a ₹6,000 limit
    becomes an ₹8,294 loss. The limit is measured on the whole book, every tick."""
    engine, broker, risk = build(book, [Scripted(signal(stop=90.0, target=130.0))],
                                 capital=100_000.0, daily_loss_limit=0.03)
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)
    quantity = engine.positions["RELIANCE"].quantity

    # Still above its stop, so position management alone would hold it — but the
    # open loss is already past the day's limit.
    mark = 100.0 - (4_000.0 / quantity)
    broker.price = mark
    engine.tick({"RELIANCE": flat_candles(price=mark)}, now=OPEN)

    assert engine.positions == {}, "an open loss past the daily limit must be closed"
    assert risk.halted is True
    assert "daily loss limit" in risk.halt_reason


def test_the_daily_stop_leaves_a_healthy_book_alone(book):
    engine, broker, risk = build(book, [Scripted(signal())], daily_loss_limit=0.03)
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    engine.tick({"RELIANCE": flat_candles(price=100.2)}, now=OPEN)

    assert "RELIANCE" in engine.positions
    assert risk.halted is False


def test_the_kill_switch_flattens_and_halts(book):
    engine, broker, risk = build(book, [Scripted(signal())])
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    engine.flatten_all({"RELIANCE": 100.0}, reason="operator pulled the plug")

    assert engine.positions == {}
    assert risk.halted is True
    assert "operator pulled the plug" in risk.halt_reason


def test_status_keeps_realized_and_unrealized_apart(book):
    """Same rule as the earner ledger: an open winner is not money you have made."""
    engine, broker, _ = build(book, [Scripted(signal())])
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    status = engine.status({"RELIANCE": 102.0})
    assert status["realized_pnl"] == 0.0
    assert status["unrealized_pnl"] > 0
    assert status["open_positions"] == 1
    assert status["live"] is False


# ── the order store in the path ─────────────────────────────────────────────

def test_every_order_is_written_down_before_it_is_sent(book, tmp_path):
    from earner.trading.orders import FILLED, OrderStore

    store = OrderStore(tmp_path / "orders.db")
    engine, broker, _ = build(book, [Scripted(signal())], orders=store)
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    orders = store.orders()
    assert len(orders) == 1
    assert orders[0].state == FILLED
    assert orders[0].broker_order_id == "stub-1", "the broker's own id is what we store"
    assert orders[0].filled_quantity == broker.orders[0]["quantity"]
    assert orders[0].average_price == 100.0
    assert orders[0].intent.startswith("scripted:entry:")
    store.close()


def test_a_broker_failure_leaves_the_order_in_an_honest_unknown_state(book, tmp_path):
    """We asked, the call failed, we do not know if it landed. Say exactly that."""
    from earner.trading.orders import UNKNOWN, OrderStore

    store = OrderStore(tmp_path / "orders.db")
    engine, broker, _ = build(book, [Scripted(signal())], orders=store)
    broker.fail_next = True
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    assert store.orders()[0].state == UNKNOWN
    assert engine.positions == {}, "a failed order is not a position"
    assert "order_failed" in stages(engine)
    store.close()


def test_the_engine_is_sized_on_the_fill_not_on_the_request(book, tmp_path):
    """A partial fill managed as a full one leaves shares behind at the close."""
    from earner.trading.orders import OrderStore

    class PartialBroker(StubBroker):
        def place(self, *, symbol, token, side, quantity, order_type="MKT", price=0.0):
            return super().place(symbol=symbol, token=token, side=side,
                                 quantity=quantity // 2, order_type=order_type, price=price)

    store = OrderStore(tmp_path / "orders.db")
    risk = RiskManager(capital=100_000.0)
    broker = PartialBroker(100.0)
    engine = Engine(broker, book, risk, session=load_session({"TRADING_MODE": "PAPER"}),
                    strategies=[Scripted(signal())], orders=store)
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    assert engine.positions["RELIANCE"].quantity == broker.orders[0]["quantity"]
    store.close()


def test_reconciliation_halts_the_engine_on_a_divergence(book, tmp_path):
    from earner.trading.orders import OrderStore, ReconciliationRequired
    from tests.test_orders import FakeBroker, remote

    store = OrderStore(tmp_path / "orders.db")
    broker = FakeBroker(orders=[remote("SOMEONE-ELSES", state="ACCEPTED", filled=0)])
    engine = Engine(broker, book, RiskManager(capital=100_000.0),
                    session=load_session({"TRADING_MODE": "PAPER"}), orders=store)

    with pytest.raises(ReconciliationRequired):
        engine.start()
    assert engine.risk.halted is True
    assert "divergence" in stages(engine)
    store.close()


def test_a_clean_reconciliation_lets_the_engine_start(book, tmp_path):
    from earner.trading.orders import OrderStore
    from tests.test_orders import FakeBroker

    store = OrderStore(tmp_path / "orders.db")
    engine = Engine(FakeBroker(orders=[], positions=[]), book, RiskManager(capital=100_000.0),
                    session=load_session({"TRADING_MODE": "PAPER"}), orders=store)

    report = engine.start()
    assert report.clean and engine.risk.halted is False
    store.close()


def test_events_are_persisted_to_the_book(book):
    """The trace has to survive the process, not just live in memory."""
    engine, _, _ = build(book, [Scripted(signal())])
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    notes = list(book.conn.execute("SELECT kind, data FROM notes"))
    assert notes and all(n["kind"] == "engine" for n in notes)


# ── stale data and the external kill switch ─────────────────────────────────

def test_a_stale_quote_blocks_a_new_entry(book):
    """`is_stale` existed for weeks and was never called from anywhere. This is
    the test that keeps it wired."""
    engine, broker, _ = build(book, [Scripted(signal())])
    engine.clock = lambda: 1_000_000.0
    engine.quote_ages["RELIANCE"] = 1_000_000.0 - 60      # a minute old

    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    assert broker.orders == []
    assert "stale_data" in stages(engine)


def test_a_fresh_quote_is_traded_normally(book):
    engine, broker, _ = build(book, [Scripted(signal())])
    engine.clock = lambda: 1_000_000.0
    engine.quote_ages["RELIANCE"] = 1_000_000.0 - 1

    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)
    assert len(broker.orders) == 1


def test_a_stale_quote_never_blocks_an_exit(book):
    """Getting out on an old price beats not getting out at all."""
    engine, broker, _ = build(book, [Scripted(signal())])
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)
    assert "RELIANCE" in engine.positions

    engine.clock = lambda: 1_000_000.0
    engine.quote_ages["RELIANCE"] = 0.0                    # maximally stale
    broker.price = 98.0
    engine.tick({"RELIANCE": flat_candles(price=98.0)}, now=OPEN)

    assert engine.positions == {}, "the stop must still fire"


def test_an_operator_can_halt_the_engine_from_outside_the_process(book, tmp_path):
    """A file, not a signal handler: it works when the terminal is gone."""
    engine, broker, risk = build(book, [Scripted(signal())])
    engine.workdir = str(tmp_path)
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)
    assert "RELIANCE" in engine.positions

    (tmp_path / "KILL").write_text("margin call")
    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)

    assert engine.positions == {}, "the kill switch flattens"
    assert risk.halted is True
    assert "margin call" in risk.halt_reason
    assert "kill_switch" in stages(engine)


def test_the_kill_switch_does_not_depend_on_the_strategy_engine(book, tmp_path):
    """Every strategy is broken here and the halt still happens."""
    engine, broker, risk = build(book, [Broken()])
    engine.workdir = str(tmp_path)
    (tmp_path / "KILL").write_text("")

    engine.tick({"RELIANCE": flat_candles()}, now=OPEN)
    assert risk.halted is True


# ── intrabar execution ──────────────────────────────────────────────────────

def _bar(open_, high, low, close):
    from earner.trading.strategy import Candle
    return Candle(at=0.0, open=open_, high=high, low=low, close=close, volume=1_000.0)


def test_a_bar_that_traded_through_the_stop_hit_the_stop(book):
    """Whatever it closed at. The close says nothing about the path."""
    position = ManagedPosition("X", BUY, 10, entry=100.0, stop=99.0, target=103.0,
                               strategy="t", thesis="")
    # Closed above the entry, but the low went through the stop.
    assert position.exit_on_bar(_bar(100.0, 101.0, 98.5, 100.8)) == ("stop", 99.0)
    assert position.exit_reason(100.8) is None, "the close alone would have missed it"


def test_an_ambiguous_bar_is_resolved_against_the_strategy_by_default(book):
    """Both touched, and the data cannot say which came first. WORST_CASE
    assumes the stop — ambiguity resolved in your favour is how a losing
    strategy passes a backtest."""
    position = ManagedPosition("X", BUY, 10, entry=100.0, stop=99.0, target=103.0,
                               strategy="t", thesis="")
    both = _bar(100.0, 103.5, 98.5, 101.0)

    assert position.exit_on_bar(both, "WORST_CASE") == ("stop", 99.0)
    assert position.exit_on_bar(both, "BEST_CASE") == ("target", 103.0)


def test_the_ohlc_path_mode_uses_the_nearer_extreme_first():
    position = ManagedPosition("X", BUY, 10, entry=100.0, stop=99.0, target=103.0,
                               strategy="t", thesis="")
    # Opened near the low, so the low is assumed to have printed first.
    assert position.exit_on_bar(_bar(99.2, 103.5, 98.5, 102.0), "OHLC_PATH") == ("stop", 99.0)
    # Opened near the high, so the target is assumed first.
    assert position.exit_on_bar(_bar(103.2, 103.5, 98.5, 100.0), "OHLC_PATH") == ("target", 103.0)


def test_a_short_is_stopped_by_the_high_not_the_low():
    position = ManagedPosition("X", SELL, 10, entry=100.0, stop=101.0, target=97.0,
                               strategy="t", thesis="")
    assert position.exit_on_bar(_bar(100.0, 101.5, 99.5, 99.8)) == ("stop", 101.0)
    assert position.exit_on_bar(_bar(100.0, 100.2, 96.5, 99.0)) == ("target", 97.0)
    assert position.exit_on_bar(_bar(100.0, 100.5, 99.5, 100.0)) is None


def test_a_quiet_bar_exits_nothing():
    position = ManagedPosition("X", BUY, 10, entry=100.0, stop=99.0, target=103.0,
                               strategy="t", thesis="")
    assert position.exit_on_bar(_bar(100.0, 100.4, 99.6, 100.2)) is None


def test_the_engine_defaults_to_worst_case(book):
    engine, _, _ = build(book, [Scripted(signal())])
    assert engine.execution_mode == "WORST_CASE"

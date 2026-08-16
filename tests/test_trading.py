"""Broker, book and risk engine. The risk tests are the important ones."""

from __future__ import annotations

import pytest

from earner.trading.broker import BUY, SELL, BrokerError, Fill, PaperBroker
from earner.trading.risk import Book, RiskManager, trading_day


@pytest.fixture
def book(tmp_path):
    b = Book(tmp_path / "book.db")
    yield b
    b.close()


# ── the book ────────────────────────────────────────────────────────────────

def test_realized_pnl_counts_only_closed_quantity(book):
    book.record(Fill("o1", "ACME", BUY, 10, 100.0))
    assert book.realized_pnl() == 0.0, "an open position is not profit"
    book.record(Fill("o2", "ACME", SELL, 10, 110.0))
    assert book.realized_pnl() == 100.0


def test_fifo_matching_on_partial_close(book):
    book.record(Fill("a", "X", BUY, 10, 100.0))
    book.record(Fill("b", "X", BUY, 10, 120.0))
    book.record(Fill("c", "X", SELL, 15, 130.0))
    assert book.realized_pnl() == 350.0          # 10*(130-100) + 5*(130-120)
    assert book.positions()["X"].quantity == 5   # 5 still open


def test_unrealized_is_kept_separate(book):
    book.record(Fill("a", "X", BUY, 10, 100.0))
    assert book.realized_pnl() == 0.0
    assert book.unrealized_pnl({"X": 150.0}) == 500.0, "open gain is not realized"


def test_a_duplicate_fill_is_recorded_once(book):
    """A websocket reconnect or retry must not double-count a fill."""
    fill = Fill("same-order-id", "X", BUY, 10, 100.0)
    assert book.record(fill) is True
    assert book.record(fill) is False
    assert len(book.fills()) == 1


# ── risk engine ─────────────────────────────────────────────────────────────

def test_size_comes_from_risk_not_from_margin():
    """1% of 100k at a 0.5% stop = 1000 risk / 0.5 per share = 2000 shares."""
    risk = RiskManager(capital=100_000, risk_per_trade=0.01, stop_loss_pct=0.005)
    assert risk.size(price=100.0) == 2000


def test_daily_loss_limit_halts_trading(book):
    risk = RiskManager(capital=100_000, daily_loss_limit=0.03)
    book.record(Fill("a", "X", BUY, 100, 1000.0))
    book.record(Fill("b", "X", SELL, 100, 960.0))     # -4,000, past the 3,000 limit

    decision = risk.check(book, price=100.0)
    assert decision.allowed is False
    assert risk.halted is True
    assert "daily loss limit" in risk.halt_reason


def test_halt_is_sticky(book):
    """Once halted, nothing reopens it inside the session."""
    risk = RiskManager(capital=100_000)
    risk.halt("manual kill switch")
    assert risk.check(book, price=100.0).allowed is False


def test_trade_count_cap_blocks_overtrading(book):
    risk = RiskManager(capital=100_000, max_trades_per_day=3)
    for i in range(3):
        book.record(Fill(f"o{i}", "X", BUY, 1, 100.0))
    decision = risk.check(book, price=100.0)
    assert decision.allowed is False and "over the 3 cap" in decision.reason


def test_max_open_positions_blocks_a_fourth(book):
    risk = RiskManager(capital=100_000, max_positions=2, max_trades_per_day=99)
    book.record(Fill("a", "AAA", BUY, 1, 100.0))
    book.record(Fill("b", "BBB", BUY, 1, 100.0))
    assert risk.check(book, price=100.0).allowed is False


def test_a_good_setup_is_allowed(book):
    risk = RiskManager(capital=100_000)
    decision = risk.check(book, price=100.0)
    assert decision.allowed is True and decision.quantity > 0


# ── paper broker ────────────────────────────────────────────────────────────

def test_paper_fills_charge_slippage_and_costs(cfg):
    """A paper engine that fills at mid with no costs is how a losing strategy
    looks profitable."""
    broker = PaperBroker(cfg, starting_capital=100_000.0)
    broker.set_price("X", 100.0)

    buy = broker.place(symbol="X", token="1", side=BUY, quantity=10)
    assert buy.price > 100.0, "slippage must work against the buyer"
    assert broker.capital < 100_000.0, "costs must be charged"

    sell = broker.place(symbol="X", token="1", side=SELL, quantity=10)
    assert sell.price < 100.0, "slippage must work against the seller too"


def test_paper_broker_is_never_live(cfg):
    broker = PaperBroker(cfg)
    assert broker.live is False
    assert broker.place.__self__.live is False


def test_paper_broker_refuses_a_price_it_does_not_have(cfg):
    with pytest.raises(BrokerError, match="No paper price"):
        PaperBroker(cfg).quote("UNKNOWN", "0")


# ── the deployment gate ─────────────────────────────────────────────────────

from earner.trading.session import (  # noqa: E402
    CONFIRMATION_PHRASE, DEVELOPMENT, GateClosed, LIVE, PAPER,
    is_stale, load_session, market_status,
)
from datetime import datetime, timedelta, timezone  # noqa: E402

IST_TZ = timezone(timedelta(hours=5, minutes=30))


def test_live_needs_both_the_mode_and_the_confirmation():
    assert load_session({"TRADING_MODE": "LIVE"}).is_live is False
    assert load_session({"TRADING_MODE": "LIVE",
                         "LIVE_TRADING_CONFIRMATION": "yes"}).is_live is False
    assert load_session({"TRADING_MODE": "LIVE",
                         "LIVE_TRADING_CONFIRMATION": CONFIRMATION_PHRASE}).is_live is True


def test_default_mode_is_not_live():
    assert load_session({}).mode == DEVELOPMENT
    assert load_session({}).is_live is False
    assert load_session({"TRADING_MODE": "PAPER"}).is_live is False


def test_require_live_raises_with_the_reason():
    with pytest.raises(GateClosed, match="TRADING_MODE=LIVE"):
        load_session({"TRADING_MODE": "PAPER"}).require_live()
    with pytest.raises(GateClosed, match="LIVE_TRADING_CONFIRMATION"):
        load_session({"TRADING_MODE": "LIVE"}).require_live()


def test_the_live_banner_is_unmissable():
    live = load_session({"TRADING_MODE": "LIVE",
                         "LIVE_TRADING_CONFIRMATION": CONFIRMATION_PHRASE})
    assert "REAL MONEY" in live.banner
    assert "PAPER" in load_session({"TRADING_MODE": PAPER}).banner


def test_a_live_order_is_refused_when_the_gate_is_shut(cfg):
    """The safety-critical test: no gate, no order, whatever else believes."""
    from earner.trading.broker import KotakBroker

    broker = KotakBroker(cfg, session=load_session({"TRADING_MODE": "PAPER"}))
    with pytest.raises(GateClosed):
        broker.place(symbol="X", token="1", side=BUY, quantity=1)


def test_an_unknown_symbol_is_never_guessed(cfg):
    from earner.trading.broker import KotakBroker

    broker = KotakBroker(cfg, session=load_session({}))
    with pytest.raises(BrokerError, match="instrument master"):
        broker.token_for("SOMETHING")


# ── market clock ────────────────────────────────────────────────────────────

def _at(h, m, day=18):  # 18 Aug 2026 is a Tuesday
    return datetime(2026, 8, day, h, m, tzinfo=IST_TZ)


def test_market_clock_gates_the_session():
    assert market_status(_at(9, 0)).open is False            # pre-open
    assert market_status(_at(10, 30)).accepting_new is True  # regular session
    assert market_status(_at(15, 5)).accepting_new is False  # near MIS cutoff
    assert market_status(_at(15, 20)).should_square_off is True
    assert market_status(_at(16, 0)).open is False           # closed
    assert market_status(_at(11, 0, day=16)).open is False   # Sunday


def test_stale_quotes_are_detectable():
    import time as _t
    assert is_stale(_t.time() - 30) is True
    assert is_stale(_t.time()) is False

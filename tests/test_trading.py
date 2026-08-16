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

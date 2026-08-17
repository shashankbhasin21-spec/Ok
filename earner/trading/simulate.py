"""A runnable session against generated intraday data.

Lets you watch the whole engine work — regime, signal, EV gate, risk veto,
fills, stops, square-off — with no broker, no credentials and no market hours.
The prices are synthetic and clearly labelled; everything downstream of them
is the production code path.
"""

from __future__ import annotations

import math
import random
import time

from datetime import datetime, timedelta

from .broker import PaperBroker
from .engine import Engine
from .orders import OrderStore, Reconciler
from .risk import Book, RiskManager
from .risk import IST
from .session import load_session
from .strategy import Candle


def generate_day(symbol_seed: int, minutes: int = 375, start: float = 1000.0,
                 trend: float = 0.0, volatility: float = 0.0025) -> list[Candle]:
    """One trading day of one-minute candles with a drift and a volume profile."""
    rng = random.Random(symbol_seed)
    candles, price, now = [], start, time.time() - minutes * 60
    for i in range(minutes):
        drift = trend / minutes
        shock = rng.gauss(0, volatility)
        close = max(1.0, price * (1 + drift + shock))
        high = max(price, close) * (1 + abs(rng.gauss(0, volatility / 2)))
        low = min(price, close) * (1 - abs(rng.gauss(0, volatility / 2)))
        # U-shaped volume: heavy at the open and the close.
        shape = 1.6 - math.sin(math.pi * i / minutes) * 0.9
        candles.append(Candle(now + i * 60, price, high, low, close,
                              rng.uniform(8_000, 16_000) * shape))
        price = close
    return candles


def run(capital: float = 200_000.0, aggressive: bool = False, workdir: str = ".earner",
        preset: str = "") -> dict:
    from pathlib import Path

    book = Book(Path(workdir) / "sim_book.db")
    orders = OrderStore(Path(workdir) / "sim_orders.db")
    session = load_session({"TRADING_MODE": "PAPER"})
    if preset == "diversified":
        risk = RiskManager.diversified(capital)
    elif aggressive or preset == "aggressive":
        risk = RiskManager.aggressive(capital)
    else:
        risk = RiskManager(capital)
    broker = PaperBroker(None, starting_capital=capital)
    # The order store is in the path here for the same reason it will be live:
    # so the run exercises write-ahead and reconciliation, not a simpler
    # code path that happens to work because nothing ever fails.
    engine = Engine(broker, book, risk, session=session, orders=orders)

    universe = {
        "RELIANCE":  generate_day(1, trend=0.018),
        "TCS":       generate_day(2, trend=-0.012),
        "HDFCBANK":  generate_day(3, trend=0.009),
        "ICICIBANK": generate_day(4, trend=0.011),
        "SUNPHARMA": generate_day(5, trend=0.002, volatility=0.0018),
    }

    print(session.banner)
    label = preset.upper() or ("AGGRESSIVE" if aggressive else "STANDARD")
    print(f"Capital ₹{capital:,.0f} · {label} risk · "
          f"{len(universe)} symbols · synthetic prices\n")

    # Replay the day minute by minute from the 40th candle onward, against a
    # simulated 09:15 open so the market clock behaves as it would live.
    open_at = datetime.now(IST).replace(hour=9, minute=15, second=0, microsecond=0)
    if open_at.weekday() >= 5:                       # roll a weekend to Monday
        open_at += timedelta(days=7 - open_at.weekday())
    for cursor in range(40, 375):
        window = {s: c[:cursor] for s, c in universe.items()}
        for symbol, candles in window.items():
            broker.set_price(symbol, candles[-1].close)
        engine.tick(window, now=open_at + timedelta(minutes=cursor))

    final = {s: c[-1].close for s, c in universe.items()}
    for symbol, price in final.items():
        broker.set_price(symbol, price)
    engine.flatten_all(final, reason="end of session")

    for event in engine.events:
        print("  " + event.line())

    # Close the day the way it must be closed live: ask the broker what it
    # thinks happened and check it against what we think happened.
    reconciliation = Reconciler(orders, None).run(broker)

    status = engine.status(final)
    print(f"\n  Realized      ₹{status['realized_pnl']:+,.0f}")
    print(f"  Trades        {status['trades_today']}")
    print(f"  Return        {status['realized_pnl'] / capital:+.2%} on capital")
    print(f"  Halted        {status['halted']}" + (f" — {status['halt_reason']}" if status['halted'] else ""))
    costs = getattr(broker, "costs_paid", 0.0)
    print(f"  Costs         ₹{costs:,.0f} ({costs / capital:.2%} of capital)")
    # The number that decides whether a small account can trade at all: costs
    # are charged per order, so they do not shrink with the account. This is
    # the gross return needed every day just to finish level.
    print(f"  Break-even    {costs / capital:+.2%}/day gross, "
          f"{costs * 250 / capital:.0%}/year, before any profit")
    print(f"  Reconciled    {reconciliation.summary()}")
    status["reconciled"] = reconciliation.clean
    book.close()
    orders.close()
    return status

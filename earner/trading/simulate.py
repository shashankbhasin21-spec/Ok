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


def run_many(days: int = 30, capital: float = 100_000.0, preset: str = "diversified",
             workdir: str = ".earner", seed: int = 0) -> dict:
    """Compound many independent days, carrying the balance forward.

    One day tells you almost nothing — the spread of outcomes is far wider than
    any single day's result. This runs the same engine over and over on fresh
    price series and reports the distribution, including the two numbers that
    actually decide the account: total cost paid, and whether the balance
    survived to the end.
    """
    from pathlib import Path
    import random as _random

    balance, trades, costs, gross = capital, 0, 0.0, 0.0
    daily, ruined_on = [], None
    rng = _random.Random(seed)

    for day in range(1, days + 1):
        if balance <= 0:
            ruined_on = ruined_on or day
            break

        book = Book(Path(workdir) / f"many_{day}.db")
        orders = OrderStore(Path(workdir) / f"many_orders_{day}.db")
        risk = (RiskManager.diversified(balance) if preset == "diversified"
                else RiskManager.aggressive(balance) if preset == "aggressive"
                else RiskManager(balance))
        broker = PaperBroker(None, starting_capital=balance)
        engine = Engine(broker, book, risk,
                        session=load_session({"TRADING_MODE": "PAPER"}), orders=orders)

        universe = {
            name: generate_day(rng.randrange(1, 10**6), trend=rng.gauss(0, 0.012))
            for name in ("RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "SUNPHARMA")
        }
        open_at = datetime.now(IST).replace(hour=9, minute=15, second=0, microsecond=0)
        if open_at.weekday() >= 5:
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

        status = engine.status(final)
        day_cost = getattr(broker, "costs_paid", 0.0)
        pnl = status["realized_pnl"]
        balance += pnl
        trades += status["trades_today"]
        costs += day_cost
        gross += pnl + day_cost
        daily.append(pnl)
        book.close()
        orders.close()
        for path in (Path(workdir) / f"many_{day}.db", Path(workdir) / f"many_orders_{day}.db"):
            path.unlink(missing_ok=True)

    wins = sum(1 for p in daily if p > 0)
    return {
        "days": len(daily),
        "trades": trades,
        "start": capital,
        "end": round(balance, 2),
        "net": round(balance - capital, 2),
        "gross_before_costs": round(gross, 2),
        "costs": round(costs, 2),
        "win_days": wins,
        "best_day": round(max(daily), 2) if daily else 0.0,
        "worst_day": round(min(daily), 2) if daily else 0.0,
        "ruined_on_day": ruined_on,
    }


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

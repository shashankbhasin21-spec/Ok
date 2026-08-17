"""Walk-forward backtest on real NSE prices.

This is the only thing that can answer whether the strategies have an edge.
Everything before it — the simulator, the risk tests, the reconciliation — was
about whether the machine works. This is about whether it makes money.

It replays real trading days through the *same* Engine, RiskManager, Book and
PaperBroker that live trading uses. Nothing is reimplemented for the backtest,
because a backtest of a reimplementation tests the reimplementation.

Three things it deliberately does not do:

* **No lookahead.** Each tick sees only bars up to that point. The engine is
  handed a growing prefix, never the full day.
* **No cost amnesty.** Slippage and per-order brokerage are charged exactly as
  in the paper broker, which is where most published backtests get their edge.
* **No survivorship editing.** Every day fetched is replayed, including the
  ones that lose, and the losing days are reported as prominently as the rest.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path

from .broker import PaperBroker
from .engine import Engine
from .marketdata import load_universe
from .risk import IST, Book, RiskManager
from .session import load_session
from .strategy import Candle

# The strategies need EMA21/ATR14/RSI14 plus an opening range before they may
# speak. On five-minute bars that is most of the morning — an honest cost of
# using the only free intraday history there is.
WARMUP_BARS = 40


@dataclass
class DayResult:
    day: str
    gross: float
    costs: float
    net: float
    trades: int
    halted: bool


@dataclass
class BacktestResult:
    days: list[DayResult] = field(default_factory=list)
    capital: float = 100_000.0
    compounded_end: float = 0.0

    @property
    def net(self) -> float:
        return round(sum(d.net for d in self.days), 2)

    @property
    def gross(self) -> float:
        return round(sum(d.gross for d in self.days), 2)

    @property
    def costs(self) -> float:
        return round(sum(d.costs for d in self.days), 2)

    @property
    def trades(self) -> int:
        return sum(d.trades for d in self.days)

    @property
    def traded_days(self) -> list[DayResult]:
        return [d for d in self.days if d.trades]

    @property
    def win_rate(self) -> float:
        traded = self.traded_days
        if not traded:
            return 0.0
        return sum(1 for d in traded if d.net > 0) / len(traded)

    @property
    def max_drawdown(self) -> float:
        """Worst peak-to-trough fall of the equity curve. What you must sit through."""
        equity, peak, worst = self.capital, self.capital, 0.0
        for day in self.days:
            equity += day.net
            peak = max(peak, equity)
            worst = min(worst, equity - peak)
        return round(worst, 2)

    def report(self) -> str:
        traded = self.traded_days
        nets = [d.net for d in traded]
        lines = [
            f"  Days tested       {len(self.days)}  ({len(traded)} with trades)",
            f"  Trades            {self.trades}",
            f"  Gross P&L         ₹{self.gross:+,.0f}   (price movement only)",
            f"  Brokerage         ₹{self.costs:,.0f}",
            f"  NET P&L           ₹{self.net:+,.0f}   ({self.net / self.capital:+.1%} on capital)",
        ]
        if traded:
            lines += [
                f"  Win rate          {self.win_rate:.0%}  ({sum(1 for n in nets if n > 0)}/{len(traded)} days)",
                f"  Average day       ₹{statistics.mean(nets):+,.0f}",
                f"  Best / worst      ₹{max(nets):+,.0f} / ₹{min(nets):+,.0f}",
                f"  Max drawdown      ₹{self.max_drawdown:,.0f}",
            ]
        return "\n".join(lines)


DEFAULT_UNIVERSE = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY",
                    "SBIN", "ITC", "SUNPHARMA", "LT", "AXISBANK"]


def run(symbols: list[str] | None = None, *, capital: float = 100_000.0,
        preset: str = "diversified", interval: str = "5m", days: int = 60,
        workdir: str = ".earner", compound: bool = False,
        verbose: bool = True) -> BacktestResult:
    symbols = symbols or DEFAULT_UNIVERSE
    if verbose:
        print(f"Fetching real NSE {interval} bars for {len(symbols)} symbols…")
    data = load_universe(symbols, interval=interval, days=days,
                         cache_dir=Path(workdir) / "cache")
    if not data:
        raise RuntimeError("no market data could be fetched")

    all_days = sorted({day for series in data.values() for day in series})
    if verbose:
        print(f"{len(all_days)} trading days, {all_days[0]} to {all_days[-1]}\n")

    result = BacktestResult(capital=capital)
    balance = capital
    scratch = Path(workdir) / "backtest"
    scratch.mkdir(parents=True, exist_ok=True)

    for day in all_days:
        session_capital = balance if compound else capital
        if session_capital <= 0:
            break

        book_path = scratch / f"{day}.db"
        book_path.unlink(missing_ok=True)
        book = Book(book_path)
        risk = (RiskManager.diversified(session_capital) if preset == "diversified"
                else RiskManager.aggressive(session_capital) if preset == "aggressive"
                else RiskManager(session_capital))
        broker = PaperBroker(None, starting_capital=session_capital)
        engine = Engine(broker, book, risk,
                        session=load_session({"TRADING_MODE": "PAPER"}))

        today = {s: series[day] for s, series in data.items() if day in series}
        if not today:
            book.close()
            continue
        longest = max(len(c) for c in today.values())

        # Walk forward: at each step the engine sees only what had printed.
        for cursor in range(WARMUP_BARS, longest + 1):
            window = {s: c[:cursor] for s, c in today.items() if len(c) >= cursor}
            if not window:
                continue
            for symbol, candles in window.items():
                broker.set_price(symbol, candles[-1].close)
            last = max(c[-1].at for c in window.values())
            engine.tick(window, now=_ist(last))

        closing = {s: c[-1].close for s, c in today.items()}
        for symbol, price in closing.items():
            broker.set_price(symbol, price)
        engine.flatten_all(closing, reason="end of day")

        gross = book.gross_pnl(book.fills()[0]["session"]) if book.fills() else 0.0
        costs = book.costs(book.fills()[0]["session"]) if book.fills() else 0.0
        net = round(gross - costs, 2)
        result.days.append(DayResult(day=day, gross=round(gross, 2), costs=round(costs, 2),
                                     net=net, trades=len(book.fills()),
                                     halted=risk.halted))
        balance += net
        book.close()
        book_path.unlink(missing_ok=True)

        if verbose and result.days[-1].trades:
            d = result.days[-1]
            print(f"  {d.day}  {d.trades:>3} trades  gross ₹{d.gross:>+9,.0f}  "
                  f"cost ₹{d.costs:>7,.0f}  net ₹{d.net:>+9,.0f}")

    result.compounded_end = round(balance, 2)
    return result


def _ist(stamp: float):
    from datetime import datetime

    return datetime.fromtimestamp(stamp, IST)

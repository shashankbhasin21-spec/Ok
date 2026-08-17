"""The live runner.

What actually happens when you point this at a real account:

    gate → connect (TOTP + MPIN, prompted, never stored) → instrument master
    → reconcile → warm up → loop → square off → reconcile

Two honest constraints, stated here rather than discovered at 09:20:

* **Warm-up.** The Kotak Neo API exposes quotes, not historical candles, so
  there is no way to backfill this morning's bars. The strategies need forty
  minutes of one-minute candles before they can say anything, which means the
  first signal of the day cannot come before about 09:55. Anything that claimed
  otherwise would be trading on indicators computed from three data points.

* **The TOTP is time-based**, so it cannot be stored and the loop cannot start
  itself unattended. That is inconvenient by design: a fully unattended live
  loop with stored credentials is one bug away from an unattended disaster.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .broker import KotakBroker
from .engine import Engine
from .orders import OrderStore, Reconciler, require_clean
from .risk import Book, RiskManager
from .session import CONFIRMATION_PHRASE, LIVE, load_session, market_status
from .strategy import Candle

# Enough history for EMA21, ATR14, RSI14 and a 15-minute opening range.
WARMUP_CANDLES = 40
POLL_SECONDS = 5.0
CANDLE_SECONDS = 60.0


@dataclass
class CandleBuilder:
    """Aggregates polled quotes into one-minute candles.

    Volume is carried from the exchange's cumulative day volume by difference,
    because the strategies use volume expansion as a confirmation and a made-up
    volume series would make that confirmation meaningless.
    """

    candles: dict[str, list[Candle]] = field(default_factory=dict)
    _open: dict[str, Candle] = field(default_factory=dict)
    _last_cumulative: dict[str, float] = field(default_factory=dict)

    def add(self, symbol: str, price: float, cumulative_volume: float = 0.0,
            now: float | None = None) -> None:
        now = now if now is not None else time.time()
        bucket = now - (now % CANDLE_SECONDS)

        previous = self._last_cumulative.get(symbol)
        self._last_cumulative[symbol] = cumulative_volume
        volume = max(0.0, cumulative_volume - previous) if previous is not None else 0.0

        current = self._open.get(symbol)
        if current is None or current.at != bucket:
            if current is not None:
                self.candles.setdefault(symbol, []).append(current)
            self._open[symbol] = Candle(at=bucket, open=price, high=price, low=price,
                                        close=price, volume=volume)
            return

        current.high = max(current.high, price)
        current.low = min(current.low, price)
        current.close = price
        current.volume += volume

    def market(self) -> dict[str, list[Candle]]:
        """Closed candles plus the one in progress — the engine sees the live bar."""
        out: dict[str, list[Candle]] = {}
        for symbol, closed in self.candles.items():
            partial = self._open.get(symbol)
            out[symbol] = closed + ([partial] if partial else [])
        for symbol, partial in self._open.items():
            out.setdefault(symbol, [partial])
        return out

    def ready(self, symbol: str) -> bool:
        return len(self.candles.get(symbol, [])) >= WARMUP_CANDLES


def preflight(cfg, universe: list[str]) -> list[str]:
    """Everything that must be true before a real order is conceivable."""
    problems = []
    session = load_session()
    if not session.is_live:
        problems.append(
            f"the gate is shut: TRADING_MODE={session.mode}, confirmation "
            f"{'present' if session.confirmed else 'missing'} — live needs "
            f"TRADING_MODE={LIVE} and LIVE_TRADING_CONFIRMATION={CONFIRMATION_PHRASE}"
        )
    for name, value in (("KOTAK_CONSUMER_KEY", cfg.kotak_consumer_key),
                        ("KOTAK_MOBILE", cfg.kotak_mobile),
                        ("KOTAK_UCC", cfg.kotak_ucc)):
        if not value:
            problems.append(f"{name} is not set")
    if not universe:
        problems.append("no symbols to trade")
    status = market_status()
    if not status.open:
        problems.append(f"market is not open: {status.reason}")
    return problems


def run_live(cfg, *, universe: list[str], capital: float, aggressive: bool = False,
             workdir: str = ".earner", poll_seconds: float = POLL_SECONDS,
             max_minutes: float | None = None) -> dict:
    """Run against a real Kotak account. Every safeguard in the package applies."""
    from getpass import getpass
    from pathlib import Path

    problems = preflight(cfg, universe)
    if problems:
        raise RuntimeError("cannot go live:\n  - " + "\n  - ".join(problems))

    session = load_session()
    print(session.banner)
    print(f"\n{len(universe)} symbols · ₹{capital:,.0f} capital · "
          f"{'AGGRESSIVE' if aggressive else 'STANDARD'} risk")
    print("Warm-up needs ~40 minutes of candles before the first signal.\n")

    broker = KotakBroker(cfg, session=session)
    # Prompted, never stored, never logged. The TOTP expires in 30 seconds.
    broker.connect(totp=getpass("TOTP from your authenticator: ").strip(),
                   mpin=getpass("MPIN: ").strip())
    count = broker.load_instruments()
    print(f"Instrument master loaded — {count:,} symbols")

    tokens = {}
    for symbol in universe:
        tokens[symbol] = broker.token_for(symbol)     # raises rather than guessing

    book = Book(Path(workdir) / "book.db")
    orders = OrderStore(Path(workdir) / "orders.db")
    risk = RiskManager.aggressive(capital) if aggressive else RiskManager(capital)
    engine = Engine(broker, book, risk, session=session, orders=orders)

    # Believe the broker over ourselves before doing anything at all.
    require_clean(engine.start())
    print("Reconciled against the broker — starting\n")

    builder = CandleBuilder()
    started = time.time()
    try:
        while True:
            status = market_status()
            if not status.open:
                print(f"Session over — {status.reason}")
                break
            if max_minutes and (time.time() - started) > max_minutes * 60:
                print("Reached the run limit — flattening")
                engine.flatten_all(_marks(builder), reason="run limit reached")
                break

            for symbol, token in tokens.items():
                try:
                    quote = broker.quote(symbol, token)
                except Exception as exc:      # a dropped quote is not a reason to stop
                    engine.log("quote_failed", f"{symbol}: {exc}", dedupe=True)
                    continue
                builder.add(symbol, quote.last_price)

            market = {s: c for s, c in builder.market().items() if len(c) >= WARMUP_CANDLES}
            if market:
                engine.tick(market)
            elif int(time.time() - started) % 300 < poll_seconds:
                have = min((len(c) for c in builder.candles.values()), default=0)
                print(f"  warming up — {have}/{WARMUP_CANDLES} candles")

            for event in engine.events[-5:]:
                print("  " + event.line())
            engine.events = engine.events[-200:]
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        print("\nInterrupted — flattening everything before exit")
        engine.flatten_all(_marks(builder), reason="operator interrupt")
    finally:
        report = Reconciler(orders, book).run(broker)
        print(f"\nFinal reconciliation: {report.summary()}")
        final = engine.status(_marks(builder))
        book.close()
        orders.close()

    return final


def _marks(builder: CandleBuilder) -> dict[str, float]:
    return {s: c[-1].close for s, c in builder.market().items() if c}

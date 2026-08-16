"""The engine loop.

    regime → strategy selection → signal → EV gate → deterministic risk veto
           → order → managed position → stop/target/square-off

The order of those stages is the whole design. The AI or the strategy may
*propose*; only the risk engine may permit (spec §5). Position management runs
independently of signal generation, so a strategy crashing cannot leave a live
position unmanaged (§18).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .broker import BUY, SELL, BrokerError, Fill
from .risk import Book, RiskManager, trading_day
from .session import TradingSession, load_session, market_status
from .strategy import ALL_STRATEGIES, Candle, Signal, classify_regime

# A trade must clear this after costs, or it is not an edge (spec §11).
MIN_EXPECTED_VALUE_PER_SHARE = 0.05
COST_PER_SHARE_ESTIMATE = 0.03

# How far the market may have moved from the price a signal was computed at,
# as a fraction of that signal's own stop distance. Past this the reward/risk
# the trade was approved on no longer describes the trade you would get.
MAX_ENTRY_DRIFT = 0.5


@dataclass
class ManagedPosition:
    """A live position with its exit plan attached. Survives strategy failure."""

    symbol: str
    side: str
    quantity: int
    entry: float
    stop: float
    target: float
    strategy: str
    thesis: str
    opened_at: float = field(default_factory=time.time)

    def exit_reason(self, price: float) -> str | None:
        if self.side == BUY:
            if price <= self.stop:
                return "stop"
            if price >= self.target:
                return "target"
        else:
            if price >= self.stop:
                return "stop"
            if price <= self.target:
                return "target"
        return None

    def unrealized(self, price: float) -> float:
        direction = 1 if self.side == BUY else -1
        return direction * (price - self.entry) * self.quantity


@dataclass
class EngineEvent:
    at: float
    stage: str
    detail: str

    def line(self) -> str:
        stamp = time.strftime("%H:%M:%S", time.localtime(self.at))
        return f"{stamp}  {self.stage:<22} {self.detail}"


class Engine:
    def __init__(self, broker, book: Book, risk: RiskManager,
                 session: TradingSession | None = None, strategies=ALL_STRATEGIES):
        self.broker = broker
        self.book = book
        self.risk = risk
        self.session = session or load_session()
        self.strategies = list(strategies)
        self.positions: dict[str, ManagedPosition] = {}
        self.events: list[EngineEvent] = []
        # Symbols closed on this pass. Re-entering the name you just exited, on
        # the bar you exited it, is how an engine pays brokerage in a loop.
        self.just_exited: set[str] = set()
        self._last_logged: dict[tuple[str, str], str] = {}

    def log(self, stage: str, detail: str, *, dedupe: bool = False) -> None:
        """Record an event. `dedupe` suppresses a repeat of the last message for
        that stage — the market clock and the halt flag say the same thing on
        every tick, and at a one-second cadence that is tens of thousands of
        identical rows a day burying the handful that matter."""
        # Keyed per stage *and* per symbol, so one symbol repeating itself does
        # not hide a different symbol saying the same thing for the first time.
        key = (stage, detail.split(":")[0])
        if dedupe and self._last_logged.get(key) == detail:
            return
        self._last_logged[key] = detail
        event = EngineEvent(time.time(), stage, detail)
        self.events.append(event)
        self.book.note("engine", stage=stage, detail=detail)

    # ------------------------------------------------------------- the cycle

    def tick(self, market: dict[str, list[Candle]], now=None) -> None:
        """One pass. `market` is symbol → candles, newest last.

        `now` is injectable so a replay can run outside market hours; live use
        leaves it None and gets the real clock.
        """
        status = market_status(now)
        marks = {s: c[-1].close for s, c in market.items() if c}
        self.just_exited.clear()

        # Position management first, and unconditionally: exits must run even
        # when the market has stopped accepting new orders.
        self.manage(marks, square_off=status.should_square_off)
        if self.enforce_daily_stop(marks):
            return

        if not status.accepting_new:
            self.log("market", f"not accepting new positions — {status.reason}", dedupe=True)
            return
        if self.risk.halted:
            self.log("risk", f"halted — {self.risk.halt_reason}", dedupe=True)
            return

        for symbol, candles in market.items():
            if symbol in self.positions or symbol in self.just_exited or not candles:
                continue
            self.consider(symbol, candles, marks)

    def enforce_daily_stop(self, marks: dict[str, float]) -> bool:
        """The daily loss limit, measured on open positions too.

        The risk manager checks the limit when a new trade is *proposed*, which
        is too late: with positions already open, the day keeps losing whether
        or not another signal ever arrives. A simulated run overshot a ₹6,000
        limit to ₹8,294 exactly that way. Here the limit is measured every tick
        against realized plus unrealized, and breaching it flattens the book.
        """
        if self.risk.halted:
            return False
        session = trading_day()
        realized = self.book.realized_pnl(session)
        unrealized = sum(
            p.unrealized(marks.get(p.symbol, p.entry)) for p in self.positions.values()
        )
        net = realized + unrealized
        limit = -abs(self.risk.capital * self.risk.daily_loss_limit)
        if net > limit:
            return False

        self.flatten_all(
            marks,
            reason=f"daily loss limit — net ₹{net:,.0f} against a ₹{limit:,.0f} limit "
                   f"(₹{realized:,.0f} realized, ₹{unrealized:,.0f} open)",
        )
        return True

    def consider(self, symbol: str, candles: list[Candle], marks: dict) -> None:
        regime = classify_regime(candles)
        if regime == "EXTREME_VOLATILITY":
            self.log("regime", f"{symbol}: extreme volatility — standing aside", dedupe=True)
            return

        # Only strategies suited to this regime get a vote (spec §10).
        signals = []
        for strategy in (s for s in self.strategies if regime in s.regimes):
            try:
                signal = strategy.evaluate(symbol, candles)
            except Exception as exc:  # noqa: BLE001 - one bad strategy, not the desk
                self.log("strategy_error", f"{symbol}: {strategy.name} raised {exc!r} — skipped")
                continue
            if signal:
                signals.append(signal)
        if not signals:
            return

        signal = max(signals, key=lambda s: s.confidence * s.reward_to_risk)
        self.log("signal", f"{symbol}: {signal.strategy} {signal.side} "
                           f"conf {signal.confidence:.0%} R:R {signal.reward_to_risk:.1f}")

        if signal.risk_per_share <= 0:
            self.log("malformed", f"{symbol}: signal has no stop distance — discarded")
            return

        # The market may have left the price this was worked out at.
        drift = abs(candles[-1].close - signal.entry)
        if drift > signal.risk_per_share * MAX_ENTRY_DRIFT:
            self.log("stale_signal", f"{symbol}: market at ₹{candles[-1].close:,.2f} is "
                                     f"{drift / signal.risk_per_share:.1f}R from the "
                                     f"₹{signal.entry:,.2f} this was sized on — discarded")
            return

        ev = signal.expected_value(signal.confidence, COST_PER_SHARE_ESTIMATE)
        if ev < MIN_EXPECTED_VALUE_PER_SHARE:
            self.log("ev_gate", f"{symbol}: EV ₹{ev:.2f}/share after costs — rejected")
            return

        decision = self.risk.check(self.book, signal.entry, symbol=symbol, marks=marks)
        if not decision.allowed:
            self.log("risk_veto", f"{symbol}: {decision.reason}", dedupe=True)
            return

        # Size from the signal's own stop, not a fixed percentage (spec §12).
        risk_budget = self.risk.capital * self.risk.risk_per_trade
        quantity = int(risk_budget / max(signal.risk_per_share, 0.01))
        quantity = min(quantity, decision.quantity)
        if quantity < 1:
            self.log("sizing", f"{symbol}: stop too wide for the risk budget")
            return

        self.enter(signal, quantity)

    def _token(self, symbol: str) -> str:
        """Resolve the broker's instrument token. Never guessed — a wrong token
        trades a different instrument than the one that was analysed."""
        resolve = getattr(self.broker, "token_for", None)
        return resolve(symbol) if resolve else ""

    def enter(self, signal: Signal, quantity: int) -> None:
        try:
            fill = self.broker.place(
                symbol=signal.symbol, token=self._token(signal.symbol),
                side=signal.side, quantity=quantity,
            )
        except BrokerError as exc:
            self.log("order_failed", f"{signal.symbol}: {exc}")
            return

        if not self.book.record(fill):
            self.log("duplicate", f"{signal.symbol}: order {fill.order_id} already booked")
            return

        self.positions[signal.symbol] = ManagedPosition(
            symbol=signal.symbol, side=signal.side, quantity=quantity, entry=fill.price,
            stop=signal.stop, target=signal.target, strategy=signal.strategy,
            thesis=signal.thesis,
        )
        self.log("filled", f"{signal.symbol} {signal.side} {quantity} @ ₹{fill.price:,.2f} "
                           f"| stop ₹{signal.stop:,.2f} target ₹{signal.target:,.2f}")

    def manage(self, marks: dict[str, float], *, square_off: bool = False) -> None:
        """Exits. Runs every tick regardless of what signal generation did."""
        for symbol, position in list(self.positions.items()):
            price = marks.get(symbol)
            if square_off:
                # A missing tick must not carry a position past the MIS cutoff:
                # the order goes at market, the price here only prices the log.
                self.exit(position, price if price is not None else position.entry, "square_off")
                continue
            if price is None:
                continue
            reason = position.exit_reason(price)
            if reason:
                self.exit(position, price, reason)

    def exit(self, position: ManagedPosition, price: float, reason: str) -> None:
        closing = SELL if position.side == BUY else BUY
        try:
            fill = self.broker.place(
                symbol=position.symbol, token=self._token(position.symbol),
                side=closing, quantity=position.quantity,
            )
        except BrokerError as exc:
            self.log("exit_failed", f"{position.symbol}: {exc} — POSITION STILL OPEN")
            return

        self.book.record(fill)
        pnl = position.unrealized(fill.price)
        self.positions.pop(position.symbol, None)
        self.just_exited.add(position.symbol)
        self.log("exit", f"{position.symbol} {reason} @ ₹{fill.price:,.2f} → "
                         f"₹{pnl:+,.0f} ({position.strategy})")

    # --------------------------------------------------------------- controls

    def flatten_all(self, marks: dict[str, float], reason: str = "emergency") -> None:
        """Kill switch (spec §19). Does not depend on strategies or a model."""
        self.log("emergency", f"flattening {len(self.positions)} position(s) — {reason}")
        for position in list(self.positions.values()):
            price = marks.get(position.symbol, position.entry)
            self.exit(position, price, "flatten")
        self.risk.halt(reason)

    def status(self, marks: dict[str, float]) -> dict:
        session = trading_day()
        realized = self.book.realized_pnl(session)
        unrealized = sum(p.unrealized(marks.get(p.symbol, p.entry)) for p in self.positions.values())
        return {
            "mode": self.session.mode,
            "live": self.session.is_live,
            "realized_pnl": realized,
            "unrealized_pnl": round(unrealized, 2),
            "net_pnl": round(realized + unrealized, 2),
            "open_positions": len(self.positions),
            "trades_today": len(self.book.fills(session)),
            "halted": self.risk.halted,
            "halt_reason": self.risk.halt_reason,
            "exposure": self.risk.exposure(self.book, marks, session).__dict__,
        }

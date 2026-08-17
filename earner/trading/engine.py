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

from .broker import BUY, SELL, BrokerError
from .orders import FILLED, DuplicateOrder, OrderStore, Reconciler, require_clean
from .risk import Book, RiskManager, trading_day
from .session import (
    TradingSession, is_stale, kill_requested, load_session, market_status,
)
from .strategy import ALL_STRATEGIES, Candle, Signal, classify_regime

# A trade must clear this after costs, or it is not an edge (spec §11).
MIN_EXPECTED_VALUE_PER_SHARE = 0.05
COST_PER_SHARE_ESTIMATE = 0.03

# How far the market may have moved from the price a signal was computed at,
# as a fraction of that signal's own stop distance. Past this the reward/risk
# the trade was approved on no longer describes the trade you would get.
MAX_ENTRY_DRIFT = 0.5

# How an ambiguous bar — one that touched both the stop and the target — is
# resolved. WORST_CASE by default and in production: never hand the strategy
# the favourable reading of something the data cannot settle.
WORST_CASE, BEST_CASE, OHLC_PATH = "WORST_CASE", "BEST_CASE", "OHLC_PATH"


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
        """Exit test against a single price. See `exit_on_bar` for real bars."""
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

    def exit_on_bar(self, bar: Candle, mode: str = "WORST_CASE") -> tuple[str, float] | None:
        """Exit test against a whole bar, which is what actually trades.

        A bar's close says nothing about the path taken to reach it. If the low
        went through the stop and the high went through the target, both were
        touched and the close is silent about which came first. Resolving that
        by whichever the close happens to be nearer is a coin flip dressed as a
        rule, and it always flatters the backtest.

        WORST_CASE assumes the stop came first whenever both were touched. It is
        the default because an assumption that costs you money when wrong is the
        only safe kind, and because ambiguity resolved in your favour is exactly
        how a losing strategy passes a backtest.
        """
        if self.side == BUY:
            hit_stop, hit_target = bar.low <= self.stop, bar.high >= self.target
        else:
            hit_stop, hit_target = bar.high >= self.stop, bar.low <= self.target

        if hit_stop and hit_target:
            if mode == "BEST_CASE":
                return "target", self.target
            if mode == "OHLC_PATH":
                # Assume the bar travelled open → nearer extreme → other extreme.
                first_low = abs(bar.open - bar.low) <= abs(bar.high - bar.open)
                stop_first = first_low if self.side == BUY else not first_low
                return ("stop", self.stop) if stop_first else ("target", self.target)
            return "stop", self.stop          # WORST_CASE
        if hit_stop:
            return "stop", self.stop
        if hit_target:
            return "target", self.target
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
                 session: TradingSession | None = None, strategies=ALL_STRATEGIES,
                 orders: OrderStore | None = None, workdir: str = ".earner"):
        self.broker = broker
        self.book = book
        self.risk = risk
        self.session = session or load_session()
        self.strategies = list(strategies)
        # Optional in paper, required before live: every order is written down
        # before it is sent, so a crash mid-flight is recoverable.
        self.orders = orders
        self.sequence = 0
        self.workdir = workdir
        self.positions: dict[str, ManagedPosition] = {}
        self.events: list[EngineEvent] = []
        # Symbols closed on this pass. Re-entering the name you just exited, on
        # the bar you exited it, is how an engine pays brokerage in a loop.
        self.just_exited: set[str] = set()
        self._last_logged: dict[tuple[str, str], str] = {}
        # symbol -> when its latest quote was observed. Empty means the caller
        # is a backtest replaying stored bars, where staleness is meaningless.
        self.quote_ages: dict[str, float] = {}
        self.clock = time.time
        self.execution_mode = WORST_CASE

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
        self.sequence += 1

        # Position management first, and unconditionally: exits must run even
        # when the market has stopped accepting new orders.
        bars = {s: c[-1] for s, c in market.items() if c}
        self.manage(marks, square_off=status.should_square_off, bars=bars)
        if self.enforce_daily_stop(marks):
            return

        # An operator can stop this from outside the process, at any moment,
        # without the strategy engine's cooperation.
        kill = kill_requested(self.workdir)
        if kill and not self.risk.halted:
            self.log("kill_switch", f"external halt requested — {kill}")
            self.flatten_all(marks, reason=f"kill switch: {kill}")
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
            # A stale quote is a price that no longer exists. Entries are
            # refused on one; exits above were not, because getting out on an
            # old price beats not getting out at all.
            if self.quote_ages and is_stale(self.quote_ages.get(symbol, 0.0),
                                            now=self.clock()):
                self.log("stale_data", f"{symbol}: quote too old to open on", dedupe=True)
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

    def send(self, symbol: str, side: str, quantity: int, intent: str):
        """Place one order, writing the intent down before sending it.

        The sequence matters and is the whole point: intent to disk, then the
        broker, then the outcome. A process that dies between the second and
        third steps restarts holding a record of an order it does not know the
        fate of — which the reconciler can resolve. Without the record it would
        simply send it again.
        """
        if self.orders is None:
            return self.broker.place(symbol=symbol, token=self._token(symbol),
                                     side=side, quantity=quantity)

        try:
            order = self.orders.open_intent(
                symbol=symbol, side=side, quantity=quantity, intent=intent)
        except DuplicateOrder as exc:
            raise BrokerError(str(exc)) from None

        try:
            fill = self.broker.place(symbol=symbol, token=self._token(symbol),
                                     side=side, quantity=quantity)
        except BrokerError as exc:
            # We do not know whether it landed. Say so, rather than guessing.
            self.orders.mark_unknown(order.client_order_id, str(exc))
            raise

        self.orders.mark_accepted(order.client_order_id, fill.order_id)
        self.orders.apply(order.client_order_id, state=FILLED,
                          filled_quantity=fill.quantity, average_price=fill.price)
        return fill

    def enter(self, signal: Signal, quantity: int) -> None:
        try:
            fill = self.send(signal.symbol, signal.side, quantity,
                             f"{signal.strategy}:entry:{self.sequence}")
        except BrokerError as exc:
            self.log("order_failed", f"{signal.symbol}: {exc}")
            return

        if not self.book.record(fill):
            self.log("duplicate", f"{signal.symbol}: order {fill.order_id} already booked")
            return

        # Sized from what was actually filled, not from what was asked for: a
        # partial fill that is managed as a full one leaves shares behind.
        self.positions[signal.symbol] = ManagedPosition(
            symbol=signal.symbol, side=signal.side, quantity=fill.quantity, entry=fill.price,
            stop=signal.stop, target=signal.target, strategy=signal.strategy,
            thesis=signal.thesis,
        )
        self.log("filled", f"{signal.symbol} {signal.side} {fill.quantity} @ ₹{fill.price:,.2f} "
                           f"| stop ₹{signal.stop:,.2f} target ₹{signal.target:,.2f}")

    def manage(self, marks: dict[str, float], *, square_off: bool = False,
               bars: dict[str, Candle] | None = None) -> None:
        """Exits. Runs every tick regardless of what signal generation did.

        When the caller supplies whole bars the stop and target are tested
        against the bar's high and low, not its close — a bar that traded
        through the stop hit the stop, whatever it closed at.
        """
        bars = bars or {}
        for symbol, position in list(self.positions.items()):
            price = marks.get(symbol)
            if square_off:
                # A missing tick must not carry a position past the MIS cutoff:
                # the order goes at market, the price here only prices the log.
                self.exit(position, price if price is not None else position.entry, "square_off")
                continue
            bar = bars.get(symbol)
            if bar is not None:
                outcome = position.exit_on_bar(bar, self.execution_mode)
                if outcome:
                    reason, fill_price = outcome
                    self.exit(position, fill_price, reason)
                continue
            if price is None:
                continue
            reason = position.exit_reason(price)
            if reason:
                self.exit(position, price, reason)

    def exit(self, position: ManagedPosition, price: float, reason: str) -> None:
        closing = SELL if position.side == BUY else BUY
        try:
            fill = self.send(position.symbol, closing, position.quantity,
                             f"{position.strategy}:{reason}:{self.sequence}")
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

    def reconcile(self, *, halt_on_divergence: bool = True):
        """Ask the broker what it thinks we own, and believe it over ourselves.

        Run at start-up, and again before the day is called finished. A
        divergence halts the engine rather than raising a warning nobody reads:
        if local state and the broker disagree, every subsequent decision is
        being made on a position size that may not exist.
        """
        if self.orders is None:
            raise RuntimeError("reconciliation needs an OrderStore — construct Engine(orders=...)")

        report = Reconciler(self.orders, self.book).run(self.broker)
        if report.clean:
            self.log("reconciled", report.summary())
            return report

        for divergence in report.divergences:
            self.log("divergence", f"{divergence.kind}: {divergence.detail}")
        if halt_on_divergence:
            self.risk.halt(f"reconciliation failed — {report.summary()}")
        return report

    def start(self):
        """Come up safely: reconcile first, and refuse to trade if it is not clean."""
        report = self.reconcile()
        require_clean(report)
        self.log("start", f"{self.session.mode} · {len(self.positions)} position(s) carried")
        return report

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

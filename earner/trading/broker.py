"""Broker layer — one interface, two implementations.

``KotakBroker`` talks to the real Kotak Neo API. ``PaperBroker`` fills orders
against the same live quotes but moves no money. They share an interface on
purpose: the strategy, the risk checks and the book cannot tell them apart, so
whatever you prove on paper is the identical code path that runs live. The only
difference is which one you construct.

Method names and parameters come from Kotak's own SDK
(github.com/Kotak-Neo/Kotak-neo-api-v2), not from memory.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol

BUY = "B"
SELL = "S"

# Kotak's own vocabulary, kept verbatim so nothing is lost in translation.
SEGMENT_EQUITY = "nse_cm"
SEGMENT_FNO = "nse_fo"
PRODUCT_INTRADAY = "MIS"
ORDER_MARKET = "MKT"
ORDER_LIMIT = "L"


class BrokerError(RuntimeError):
    """The broker rejected the request, or is not connected."""


@dataclass
class Quote:
    symbol: str
    last_price: float
    bid: float = 0.0
    ask: float = 0.0
    at: float = field(default_factory=time.time)


@dataclass
class Fill:
    order_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    at: float = field(default_factory=time.time)
    paper: bool = True
    # Brokerage, STT, exchange fees, GST and stamp duty for this fill. Carried
    # on the fill rather than tracked beside it because a P&L that excludes
    # costs is not a P&L, and a daily loss limit measured against one stops
    # later than it promised.
    cost: float = 0.0

    @property
    def value(self) -> float:
        return self.quantity * self.price


class Broker(Protocol):
    live: bool

    def connect(self) -> None: ...
    def quote(self, symbol: str, token: str) -> Quote: ...
    def place(self, *, symbol: str, token: str, side: str, quantity: int,
              order_type: str = ORDER_MARKET, price: float = 0.0) -> Fill: ...
    def order_book(self) -> list: ...
    def positions(self) -> list[dict]: ...
    def available_margin(self) -> float: ...


def _sdk_error(response) -> str:
    """The Kotak SDK catches its own exceptions and returns them as data.

    ``place_order`` never raises: it answers ``{'Error': ...}`` on failure and
    ``{"Error Message": "Complete the 2fa process..."}`` when the session was
    never validated. Both look like a successful call to anything that only
    checks for an exception, so every response is inspected here instead.
    """
    if not isinstance(response, dict):
        return f"unexpected response type {type(response).__name__}: {response!r}"
    for key in ("Error", "Error Message", "error", "errMsg", "message"):
        if response.get(key):
            return str(response[key])
    if str(response.get("stat", "")).lower() in ("not_ok", "notok", "error"):
        return str(response)
    return ""


class KotakBroker:
    """The real thing. Places real orders with real money.

    Auth is Kotak's two-step TOTP flow: ``totp_login`` returns a view token,
    ``totp_validate`` exchanges the MPIN for the trade token that authorises
    orders. The TOTP is time-based, so it cannot be stored — it is supplied at
    connect time, which is also a useful brake on a fully unattended loop.
    """

    live = True

    def __init__(self, cfg, session=None):
        from .session import load_session

        self.cfg = cfg
        self.client = None
        # The gate. Nothing else in this class may authorise real money.
        self.session = session or load_session()
        self._instruments: dict[str, str] = {}

    def connect(self, *, totp: str, mpin: str) -> None:
        try:
            from neo_api_client import NeoAPI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise BrokerError(
                'pip install "git+https://github.com/Kotak-Neo/Kotak-neo-api-v2.git'
                '@v2.0.2#egg=neo_api_client"'
            ) from exc

        if not (self.cfg.kotak_consumer_key and self.cfg.kotak_mobile and self.cfg.kotak_ucc):
            raise BrokerError(
                "Set KOTAK_CONSUMER_KEY, KOTAK_MOBILE and KOTAK_UCC. The consumer key is in "
                "the Neo app under Invest → Trade API."
            )

        self.client = NeoAPI(
            environment="prod",
            access_token=None,
            neo_fin_key=None,
            consumer_key=self.cfg.kotak_consumer_key,
        )
        self.client.totp_login(
            mobile_number=self.cfg.kotak_mobile, ucc=self.cfg.kotak_ucc, totp=totp
        )
        self.client.totp_validate(mpin=mpin)

    def _require(self):
        if self.client is None:
            raise BrokerError("Not connected — call connect() with a fresh TOTP first")
        return self.client

    def quote(self, symbol: str, token: str) -> Quote:
        data = self._require().quotes(
            instrument_tokens=[{"instrument_token": token, "exchange_segment": SEGMENT_EQUITY}],
            quote_type="ltp",
        )
        rows = data.get("message") or data.get("data") or []
        if not rows:
            raise BrokerError(f"No quote returned for {symbol}")
        row = rows[0]
        last = float(row.get("last_traded_price") or row.get("ltp") or 0)
        if not last:
            raise BrokerError(f"Quote for {symbol} had no last traded price")
        return Quote(symbol=symbol, last_price=last)

    def load_instruments(self, segment: str = SEGMENT_EQUITY) -> int:
        """Download the scrip master so symbols resolve to real tokens.

        Trading a token you guessed is trading a different instrument than the
        one you analysed, so nothing is placed until this has run.
        """
        data = self._require().scrip_master(exchange_segment=segment)
        rows = data if isinstance(data, list) else (data.get("data") or [])
        for row in rows:
            symbol = row.get("pTrdSymbol") or row.get("trading_symbol")
            token = row.get("pSymbol") or row.get("instrument_token")
            if symbol and token:
                self._instruments[str(symbol).upper()] = str(token)
        return len(self._instruments)

    def token_for(self, symbol: str) -> str:
        key = symbol.upper()
        if key not in self._instruments:
            raise BrokerError(
                f"{symbol} is not in the instrument master — run load_instruments() first, "
                "and never guess a token"
            )
        return self._instruments[key]

    def submit(self, *, symbol, token, side, quantity, order_type=ORDER_MARKET,
               price=0.0, tag="earner") -> str:
        """Send the order. Returns the broker's order number — not a fill.

        This is deliberately the only method that talks to ``place_order``, and
        deliberately does not pretend to know the outcome: Kotak answers with
        ``{"stat": "Ok", "nOrdNo": "..."}`` and nothing about price or quantity
        done. Ask ``confirm`` what actually happened.
        """
        # Re-checked here rather than trusted from construction: a long-running
        # engine can outlive the assumptions it started with.
        self.session.require_live()

        from .session import market_status

        status = market_status()
        if not status.accepting_new:
            raise BrokerError(f"market not accepting new orders: {status.reason}")

        response = self._require().place_order(
            exchange_segment=SEGMENT_EQUITY,
            product=PRODUCT_INTRADAY,
            price=str(price or 0),
            order_type=order_type,
            quantity=str(quantity),
            validity="DAY",
            trading_symbol=symbol,
            transaction_type=side,
            amo="NO",
            disclosed_quantity="0",
            market_protection="0",
            pf="N",
            trigger_price="0",
            tag=tag,
        )
        problem = _sdk_error(response)
        if problem:
            raise BrokerError(f"{symbol} {side} {quantity} rejected: {problem}")
        order_id = str(response.get("nOrdNo") or response.get("orderId") or "")
        if not order_id:
            raise BrokerError(f"{symbol} {side} {quantity} returned no order number: {response}")
        return order_id

    def confirm(self, order_id: str, *, timeout: float = 10.0,
                interval: float = 0.5):
        """Poll the order book until this order is done, and report what it did.

        Returns the broker's own row. If the order is still working when the
        timeout expires it raises — leaving the order in flight for the
        reconciler rather than inventing an answer.
        """
        from .orders import TERMINAL

        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            for row in self.order_book():
                if row.broker_order_id == str(order_id):
                    last = row
                    if row.state in TERMINAL:
                        return row
                    break
            time.sleep(interval)

        if last is None:
            raise BrokerError(
                f"order {order_id} was accepted but never appeared in the order book "
                "— reconcile before placing anything else"
            )
        raise BrokerError(
            f"order {order_id} still {last.state} after {timeout:.0f}s "
            f"({last.filled_quantity}/{last.quantity} done) — left in flight for reconciliation"
        )

    def place(self, *, symbol, token, side, quantity, order_type=ORDER_MARKET, price=0.0) -> Fill:
        """Submit and confirm. The returned price is the exchange's, never a quote."""
        order_id = self.submit(symbol=symbol, token=token, side=side, quantity=quantity,
                               order_type=order_type, price=price)
        row = self.confirm(order_id)
        from .orders import FILLED

        if row.state != FILLED or row.filled_quantity <= 0:
            why = f": {row.reject_reason}" if row.reject_reason else ""
            raise BrokerError(
                f"order {order_id} ended {row.state} "
                f"({row.filled_quantity}/{row.quantity} done){why}"
            )
        # Kotak does not return charges on the order; the contract note settles
        # them overnight. Estimating is far better than recording zero, which
        # would make the daily loss limit stop later than it promised.
        estimated = row.filled_quantity * row.average_price * PaperBroker.COST_RATE \
            + PaperBroker.FIXED_COST_PER_ORDER
        return Fill(order_id=order_id, symbol=symbol, side=side,
                    quantity=row.filled_quantity, price=row.average_price, paper=False,
                    cost=round(estimated, 2))

    def cancel(self, order_id: str) -> None:
        response = self._require().cancel_order(order_id=str(order_id), isVerify=True)
        problem = _sdk_error(response)
        if problem:
            raise BrokerError(f"could not cancel {order_id}: {problem}")

    def order_book(self) -> list:
        """The broker's own order book, translated into our vocabulary."""
        from .orders import normalise_kotak_order

        response = self._require().order_report()
        problem = _sdk_error(response)
        if problem:
            raise BrokerError(f"could not read the order book: {problem}")
        rows = response if isinstance(response, list) else (response.get("data") or [])
        return [normalise_kotak_order(row) for row in rows]

    def positions(self) -> list[dict]:
        data = self._require().positions()
        return data.get("data") or []

    def available_margin(self) -> float:
        data = self._require().limits(segment="ALL", exchange="ALL", product="ALL")
        for key in ("Net", "net", "MarginAvailable", "CollateralValue"):
            if key in data:
                try:
                    return float(data[key])
                except (TypeError, ValueError):
                    continue
        raise BrokerError(f"Could not read available margin from limits response: {data}")


class PaperBroker:
    """Fills against real quotes, moves no money.

    Slippage and brokerage are charged deliberately. A paper engine that fills
    at the mid with zero costs is the single most common way a strategy looks
    profitable on paper and loses live — SEBI's data puts the cost drag at 57%
    of loss-makers' losses, so leaving it out would be modelling a market that
    does not exist.
    """

    live = False

    # Rough all-in intraday cost: brokerage, STT, exchange fees, GST, stamp duty.
    COST_RATE = 0.0005      # 5 bps per side
    SLIPPAGE_RATE = 0.0003  # 3 bps against you on a market order
    # Flat charge per executed order. Set this to your actual plan — it is the
    # cost that decides whether a small account can trade at all. On ₹1,00,000
    # with ten positions, a round trip is twenty orders: ₹400 of fixed cost
    # before the first rupee of profit, which a percentage-only model hides
    # completely.
    FIXED_COST_PER_ORDER = 20.0

    def __init__(self, cfg, quote_source=None, starting_capital: float = 100_000.0):
        self.cfg = cfg
        self.capital = starting_capital
        # A real broker for quotes if you have one; otherwise inject prices.
        self.quote_source = quote_source
        self._prices: dict[str, float] = {}
        self.fills: list[Fill] = []

    def connect(self, **_kwargs) -> None:
        if self.quote_source is not None:
            self.quote_source.connect(**_kwargs)

    def set_price(self, symbol: str, price: float) -> None:
        """Feed a price when running without a live quote source (tests, backtests)."""
        self._prices[symbol] = price

    def quote(self, symbol: str, token: str) -> Quote:
        if self.quote_source is not None:
            return self.quote_source.quote(symbol, token)
        if symbol not in self._prices:
            raise BrokerError(f"No paper price for {symbol}")
        return Quote(symbol=symbol, last_price=self._prices[symbol])

    def place(self, *, symbol, token, side, quantity, order_type=ORDER_MARKET, price=0.0) -> Fill:
        mid = price or self.quote(symbol, token).last_price
        # Slippage always works against you, whichever way you are going.
        fill_price = mid * (1 + self.SLIPPAGE_RATE) if side == BUY else mid * (1 - self.SLIPPAGE_RATE)
        cost = fill_price * quantity * self.COST_RATE + self.FIXED_COST_PER_ORDER
        self.capital -= cost
        self.costs_paid = getattr(self, "costs_paid", 0.0) + cost

        fill = Fill(
            order_id=f"paper-{uuid.uuid4().hex[:10]}", symbol=symbol, side=side,
            quantity=quantity, price=round(fill_price, 2), paper=True,
            cost=round(cost, 2),
        )
        self.fills.append(fill)
        return fill

    def submit(self, *, symbol, token, side, quantity, order_type=ORDER_MARKET,
               price=0.0, tag="earner") -> str:
        return self.place(symbol=symbol, token=token, side=side, quantity=quantity,
                          order_type=order_type, price=price).order_id

    def cancel(self, order_id: str) -> None:
        raise BrokerError(f"paper order {order_id} filled immediately — nothing to cancel")

    def order_book(self) -> list:
        """Paper orders fill on submission, so every one of them is complete.

        Shaped identically to the live order book so the reconciler runs against
        paper exactly as it will run against Kotak.
        """
        from .orders import FILLED, BrokerOrder

        return [
            BrokerOrder(
                broker_order_id=f.order_id, symbol=f.symbol, side=f.side,
                quantity=f.quantity, filled_quantity=f.quantity,
                average_price=f.price, state=FILLED,
            )
            for f in self.fills
        ]

    def positions(self) -> list[dict]:
        net: dict[str, int] = {}
        for fill in self.fills:
            net[fill.symbol] = net.get(fill.symbol, 0) + (
                fill.quantity if fill.side == BUY else -fill.quantity
            )
        return [{"symbol": s, "quantity": q} for s, q in net.items() if q]

    def available_margin(self) -> float:
        return self.capital

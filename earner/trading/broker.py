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

    @property
    def value(self) -> float:
        return self.quantity * self.price


class Broker(Protocol):
    live: bool

    def connect(self) -> None: ...
    def quote(self, symbol: str, token: str) -> Quote: ...
    def place(self, *, symbol: str, token: str, side: str, quantity: int,
              order_type: str = ORDER_MARKET, price: float = 0.0) -> Fill: ...
    def positions(self) -> list[dict]: ...
    def available_margin(self) -> float: ...


class KotakBroker:
    """The real thing. Places real orders with real money.

    Auth is Kotak's two-step TOTP flow: ``totp_login`` returns a view token,
    ``totp_validate`` exchanges the MPIN for the trade token that authorises
    orders. The TOTP is time-based, so it cannot be stored — it is supplied at
    connect time, which is also a useful brake on a fully unattended loop.
    """

    live = True

    def __init__(self, cfg):
        self.cfg = cfg
        self.client = None

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

    def place(self, *, symbol, token, side, quantity, order_type=ORDER_MARKET, price=0.0) -> Fill:
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
            tag="earner",
        )
        order_id = str(response.get("nOrdNo") or response.get("orderId") or "")
        if not order_id:
            raise BrokerError(f"Order rejected: {response}")
        return Fill(
            order_id=order_id, symbol=symbol, side=side, quantity=quantity,
            price=price or self.quote(symbol, token).last_price, paper=False,
        )

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
        cost = fill_price * quantity * self.COST_RATE
        self.capital -= cost

        fill = Fill(
            order_id=f"paper-{uuid.uuid4().hex[:10]}", symbol=symbol, side=side,
            quantity=quantity, price=round(fill_price, 2), paper=True,
        )
        self.fills.append(fill)
        return fill

    def positions(self) -> list[dict]:
        net: dict[str, int] = {}
        for fill in self.fills:
            net[fill.symbol] = net.get(fill.symbol, 0) + (
                fill.quantity if fill.side == BUY else -fill.quantity
            )
        return [{"symbol": s, "quantity": q} for s, q in net.items() if q]

    def available_margin(self) -> float:
        return self.capital

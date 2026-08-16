"""Trading modes and the live-money gate.

Three modes. LIVE is unreachable by accident: it needs two environment
variables set to exact values, and the confirmation string is deliberately
long enough that nobody types it without meaning it.

    TRADING_MODE=LIVE
    LIVE_TRADING_CONFIRMATION=I_UNDERSTAND_REAL_MONEY

Missing or wrong: no real order can be placed, whatever anything else in the
process believes. The gate is checked at construction *and* again immediately
before every order, because a long-running engine can outlive the assumptions
it started with.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime

from .risk import IST

DEVELOPMENT = "DEVELOPMENT"
PAPER = "PAPER"
LIVE = "LIVE"

CONFIRMATION_PHRASE = "I_UNDERSTAND_REAL_MONEY"

# NSE equity regular session.
MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
# Intraday (MIS) positions are squared off by the broker around 3:20pm; stop
# opening new ones well before that so exits are not taken at the auction.
NO_NEW_POSITIONS_AFTER = dtime(15, 0)
SQUARE_OFF_AT = dtime(15, 15)


class GateClosed(RuntimeError):
    """A live order was attempted without the deployment gate open."""


@dataclass(frozen=True)
class TradingSession:
    mode: str
    confirmed: bool

    @property
    def is_live(self) -> bool:
        """The only thing in the codebase permitted to authorise real money."""
        return self.mode == LIVE and self.confirmed

    @property
    def banner(self) -> str:
        if self.is_live:
            return "\033[1;97;41m 🔴 LIVE TRADING — REAL MONEY \033[0m"
        if self.mode == LIVE and not self.confirmed:
            return (
                "\033[1;30;43m ⚠ TRADING_MODE=LIVE but confirmation missing — "
                "no real orders \033[0m"
            )
        if self.mode == PAPER:
            return "\033[1;30;46m ◆ PAPER — simulated fills, real prices \033[0m"
        return "\033[1;37;44m ◆ DEVELOPMENT — no broker calls \033[0m"

    def require_live(self) -> None:
        """Called immediately before every real order. Raises rather than warns."""
        if self.is_live:
            return
        if self.mode != LIVE:
            raise GateClosed(f"TRADING_MODE={self.mode} — live orders need TRADING_MODE=LIVE")
        raise GateClosed(
            f"TRADING_MODE=LIVE but LIVE_TRADING_CONFIRMATION is not "
            f"'{CONFIRMATION_PHRASE}' — refusing to place a real order"
        )


def load_session(env: dict | None = None) -> TradingSession:
    env = env if env is not None else os.environ
    mode = (env.get("TRADING_MODE") or DEVELOPMENT).strip().upper()
    if mode not in (DEVELOPMENT, PAPER, LIVE):
        raise ValueError(f"TRADING_MODE must be DEVELOPMENT, PAPER or LIVE — got {mode!r}")
    confirmed = env.get("LIVE_TRADING_CONFIRMATION", "").strip() == CONFIRMATION_PHRASE
    return TradingSession(mode=mode, confirmed=confirmed)


# ── market clock ────────────────────────────────────────────────────────────

@dataclass
class MarketStatus:
    open: bool
    accepting_new: bool
    should_square_off: bool
    reason: str


def market_status(now: datetime | None = None) -> MarketStatus:
    """Where we are in the session. Checked before every order (spec §16)."""
    now = now or datetime.now(IST)
    clock = now.time()

    if now.weekday() >= 5:
        return MarketStatus(False, False, False, "weekend")
    if clock < MARKET_OPEN:
        return MarketStatus(False, False, False, f"pre-open (opens {MARKET_OPEN:%H:%M})")
    if clock >= MARKET_CLOSE:
        return MarketStatus(False, False, False, "closed")
    if clock >= SQUARE_OFF_AT:
        return MarketStatus(True, False, True, "square-off window — flattening only")
    if clock >= NO_NEW_POSITIONS_AFTER:
        return MarketStatus(
            True, False, False,
            f"no new positions after {NO_NEW_POSITIONS_AFTER:%H:%M} — too near the MIS cutoff",
        )
    return MarketStatus(True, True, False, "open")


# ── data freshness ──────────────────────────────────────────────────────────

def is_stale(quote_time: float, *, max_age_seconds: float = 5.0) -> bool:
    """Trading on a stale quote is trading on a price that no longer exists."""
    return (time.time() - quote_time) > max_age_seconds

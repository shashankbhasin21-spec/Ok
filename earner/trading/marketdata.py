"""Real historical NSE prices.

The engine has been run against synthetic random walks, which prove the
plumbing and prove nothing about the strategies: a random walk has no edge in
it, so every strategy loses on one. Deciding whether these strategies work
needs real prices that real people traded at.

Source is Yahoo Finance's chart endpoint, which carries NSE equities under the
``.NS`` suffix and serves intraday bars back about sixty days. It is free, it
needs no key, and it is good enough to answer the only question that matters
before real money is committed: does this make money on prices that actually
happened?

Downloaded days are cached to disk. A backtest you cannot re-run identically
is an anecdote, not a result.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from .risk import IST
from .strategy import Candle

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS"
USER_AGENT = "Mozilla/5.0 (compatible; earner-backtest/1.0)"

# NSE regular session, as seconds since midnight IST.
SESSION_OPEN = 9 * 3600 + 15 * 60
SESSION_CLOSE = 15 * 3600 + 30 * 60


class DataError(RuntimeError):
    """The data could not be fetched, or arrived unusable."""


def fetch(symbol: str, *, interval: str = "5m", days: int = 60,
          cache_dir: str | Path = ".earner/cache") -> list[Candle]:
    """Real bars for one NSE symbol, newest last. Cached for a day."""
    cache = Path(cache_dir) / f"{symbol.upper()}_{interval}_{days}d.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 86_400:
        return _to_candles(json.loads(cache.read_text()))

    url = CHART_URL.format(symbol=symbol.upper()) + f"?interval={interval}&range={days}d"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise DataError(f"could not fetch {symbol}: {exc}") from exc

    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        error = (payload.get("chart") or {}).get("error")
        raise DataError(f"no data for {symbol}: {error}")

    cache.write_text(json.dumps(result[0]))
    return _to_candles(result[0])


def _to_candles(result: dict) -> list[Candle]:
    """Yahoo's parallel arrays into candles, with the gaps dropped.

    Bars where any field is null are discarded rather than filled. A
    forward-filled bar is a price nobody traded at, and a strategy that trades
    off one is being tested against fiction.
    """
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens, highs = quote.get("open") or [], quote.get("high") or []
    lows, closes = quote.get("low") or [], quote.get("close") or []
    volumes = quote.get("volume") or []

    candles = []
    for i, stamp in enumerate(stamps):
        try:
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            v = volumes[i]
        except IndexError:
            continue
        if None in (o, h, l, c):
            continue
        candles.append(Candle(at=float(stamp), open=float(o), high=float(h),
                              low=float(l), close=float(c), volume=float(v or 0)))
    return candles


def by_day(candles: list[Candle]) -> dict[str, list[Candle]]:
    """Split a series into trading days, keeping only regular-session bars."""
    days: dict[str, list[Candle]] = {}
    for candle in candles:
        when = datetime.fromtimestamp(candle.at, IST)
        seconds = when.hour * 3600 + when.minute * 60
        if not (SESSION_OPEN <= seconds < SESSION_CLOSE):
            continue
        days.setdefault(when.strftime("%Y-%m-%d"), []).append(candle)
    return days


def load_universe(symbols: list[str], *, interval: str = "5m", days: int = 60,
                  cache_dir: str | Path = ".earner/cache") -> dict[str, dict[str, list[Candle]]]:
    """symbol → day → candles, for every symbol that could be fetched."""
    out: dict[str, dict[str, list[Candle]]] = {}
    for symbol in symbols:
        try:
            out[symbol] = by_day(fetch(symbol, interval=interval, days=days,
                                       cache_dir=cache_dir))
        except DataError as exc:
            print(f"  ! {symbol}: {exc}")
    return out

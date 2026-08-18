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


# What each interval can actually reach, measured rather than assumed. The
# asymmetry here is the most important fact in this repository: intraday
# history is capped at weeks, daily history runs to decades, and a strategy
# that holds overnight can therefore be validated while an intraday one cannot.
#
#     1m   ~7 days        (~2,300 bars)
#     5m   ~60 days       (~4,400 bars)
#     1h   ~2.9 years     (~5,100 bars)
#     1d   20+ years      (~4,900 bars)
#
# Nine hundred observations are needed to separate a Sharpe-1 edge from luck
# across a modest search. Only the last two rows can supply that.
MAX_RANGE = {"1m": "7d", "2m": "60d", "5m": "60d", "15m": "60d",
             "30m": "60d", "1h": "730d", "1d": "20y", "1wk": "20y"}


def fetch(symbol: str, *, interval: str = "5m", days: int = 60,
          cache_dir: str | Path = ".earner/cache") -> list[Candle]:
    """Real bars for one NSE symbol, newest last. Cached for a day.

    For daily bars `days` is interpreted generously — pass 5000 to ask for
    twenty years — because the useful unit there is years, not sessions.
    """
    cache = Path(cache_dir) / f"{symbol.upper()}_{interval}_{days}d.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 86_400:
        return _to_candles(json.loads(cache.read_text()))

    if interval in ("1d", "1wk"):
        # Yahoo caps a "Nd" range; years are the correct unit for daily bars,
        # and "max" silently downgrades to monthly, which is not daily data.
        span = f"{max(1, min(20, round(days / 250)))}y"
    else:
        span = f"{days}d"
    url = CHART_URL.format(symbol=symbol.upper()) + f"?interval={interval}&range={span}"
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


def by_day(candles: list[Candle], *, intraday: bool = True) -> dict[str, list[Candle]]:
    """Split a series into trading days.

    `intraday` keeps only regular-session bars, which is right for minute and
    hour data. Daily bars carry a single stamp per session that need not fall
    inside 09:15-15:30, so filtering them the same way discards the entire
    series — a silent empty result rather than an error.
    """
    days: dict[str, list[Candle]] = {}
    for candle in candles:
        when = datetime.fromtimestamp(candle.at, IST)
        if intraday:
            seconds = when.hour * 3600 + when.minute * 60
            if not (SESSION_OPEN <= seconds < SESSION_CLOSE):
                continue
        days.setdefault(when.strftime("%Y-%m-%d"), []).append(candle)
    return days


def daily_series(symbol: str, *, years: int = 20,
                 cache_dir: str | Path = ".earner/cache") -> list[Candle]:
    """Years of daily bars for one symbol, newest last.

    The counterpart to the intraday loader, and the one that makes validation
    arithmetically possible: twenty years is roughly 4,900 observations against
    the ~900 a credible search needs.
    """
    return fetch(symbol, interval="1d", days=years * 250, cache_dir=cache_dir)


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

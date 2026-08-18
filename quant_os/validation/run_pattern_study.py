"""Run the pattern study across a wide universe and deflate the result."""
from __future__ import annotations

import json
import statistics
import urllib.request
from datetime import datetime
from pathlib import Path

from earner.trading.strategy import Candle
from quant_os.validation.metrics import deflated_sharpe, expected_max_sharpe
from quant_os.validation.pattern_study import BEARISH, BULLISH, PATTERNS, study

CACHE = Path(".earner/cache/daily")

INDIA = ["RELIANCE.NS","TCS.NS","HDFCBANK.NS","ICICIBANK.NS","INFY.NS","SBIN.NS",
         "ITC.NS","LT.NS","AXISBANK.NS","KOTAKBANK.NS","BHARTIARTL.NS","ASIANPAINT.NS",
         "MARUTI.NS","SUNPHARMA.NS","TITAN.NS","ULTRACEMCO.NS","WIPRO.NS","NESTLEIND.NS",
         "HINDUNILVR.NS","BAJFINANCE.NS","M&M.NS","TATAMOTORS.NS","TATASTEEL.NS",
         "POWERGRID.NS","NTPC.NS","ONGC.NS","COALINDIA.NS","GRASIM.NS","CIPLA.NS",
         "DRREDDY.NS","JSWSTEEL.NS","HINDALCO.NS","BPCL.NS","EICHERMOT.NS","HEROMOTOCO.NS",
         "BRITANNIA.NS","DIVISLAB.NS","ADANIPORTS.NS","TECHM.NS","HCLTECH.NS"]
INDICES = ["^NSEI","^NSEBANK","^BSESN","^GSPC","^IXIC","^DJI","^RUT","^N225",
           "^FTSE","^GDAXI","^FCHI","^HSI","000001.SS","^AXJO","^KS11","^BVSP"]
OTHER   = ["GC=F","CL=F","SI=F","BTC-USD","ETH-USD","EURUSD=X","USDINR=X"]
UNIVERSE = INDIA + INDICES + OTHER


def load_safe(symbol: str) -> list[Candle]:
    """load() but returns [] instead of raising. Delisted and renamed tickers
    404, and one dead symbol must not abort a study over sixty instruments."""
    try:
        return load(symbol)
    except Exception:
        return []


def load(symbol: str) -> list[Candle]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol.replace('/', '_')}.json"
    if path.exists():
        raw = json.loads(path.read_text())
    else:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
               f"?interval=1d&period1=0&period2=9999999999")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = json.load(r)["chart"]["result"][0]
        path.write_text(json.dumps(raw))
    ts = raw.get("timestamp") or []
    q = ((raw.get("indicators") or {}).get("quote") or [{}])[0]
    o, h, l, c = q.get("open") or [], q.get("high") or [], q.get("low") or [], q.get("close") or []
    v = q.get("volume") or []
    out = []
    for i, stamp in enumerate(ts):
        try:
            if None in (o[i], h[i], l[i], c[i]):
                continue
            out.append(Candle(float(stamp), float(o[i]), float(h[i]),
                              float(l[i]), float(c[i]), float(v[i] or 0)))
        except IndexError:
            continue
    return out


def run(horizons=(1, 3, 5, 10, 20), verbose=True):
    data, total_bars = {}, 0
    for symbol in UNIVERSE:
        try:
            series = load(symbol)
            if len(series) > 600:
                data[symbol] = series
                total_bars += len(series)
        except Exception as exc:
            if verbose:
                print(f"  ! {symbol}: {exc}")
    if verbose:
        years = total_bars / 252
        print(f"\nUniverse: {len(data)} instruments, {total_bars:,} daily bars "
              f"({years:,.0f} instrument-years)\n")

    results = []
    for name, fn in PATTERNS.items():
        short = name in BEARISH and name not in BULLISH
        for horizon in horizons:
            pooled, occurrences = [], 0
            for symbol, series in data.items():
                r = study(series, fn, horizon, short=short)
                if r:
                    pooled.append(r)
                    occurrences += r.occurrences
            if not pooled:
                continue
            excess = statistics.mean(p.excess for p in pooled)
            spread = statistics.mean(p.stdev for p in pooled)
            results.append(dict(
                pattern=name, horizon=horizon, instruments=len(pooled),
                occurrences=occurrences, excess=excess,
                net_excess=excess - 0.0022,
                sharpe=excess / spread if spread else 0.0,
                hit=statistics.mean(p.hit_rate for p in pooled),
                base_hit=statistics.mean(p.base_hit_rate for p in pooled),
            ))
    return results, data, total_bars

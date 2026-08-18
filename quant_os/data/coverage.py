"""What history actually exists, measured rather than assumed.

The brief asks for ~90 years. This module's job is to report honestly where
that is available and where it is not, because a research system that quietly
substitutes 20 years for 90, or monthly bars for daily, produces confident
answers to questions it never asked.

Three findings this produces that shape everything downstream:

* Daily history reaches decades. Intraday history reaches weeks. That
  asymmetry, not engineering effort, decides which strategies can be validated.
* ~90 years exists only for a handful of US indices. Indian equity history
  starts in the 1990s because the exchanges did.
* Yahoo's ``range=max`` with ``interval=1d`` silently returns MONTHLY bars.
  Anything trusting the label would believe it had 30 years of daily data and
  be wrong by a factor of 21.
"""

from __future__ import annotations

import json
import statistics
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
AGENT = "Mozilla/5.0 (compatible; quant-os-coverage/1.0)"
CACHE = Path(".earner/cache/daily")


@dataclass
class Coverage:
    symbol: str
    label: str
    bars: int = 0
    first: str = ""
    last: str = ""
    years: float = 0.0
    median_gap_days: float = 0.0
    gaps_over_week: int = 0
    zero_volume_bars: int = 0
    duplicate_stamps: int = 0
    error: str = ""
    issues: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return not self.error and self.bars > 500 and self.median_gap_days < 5

    @property
    def is_daily(self) -> bool:
        """Guards the trap: monthly bars wearing a daily label."""
        return 0.5 < self.median_gap_days < 5


def fetch_raw(symbol: str, *, interval: str = "1d", cache: Path = CACHE) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{symbol.replace('/', '_')}.json"
    if interval == "1d" and path.exists():
        return json.loads(path.read_text())
    url = CHART.format(symbol=symbol) + (
        f"?interval={interval}&period1=0&period2=9999999999" if interval == "1d"
        else f"?interval={interval}&range=60d")
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=40) as response:
        raw = json.load(response)["chart"]["result"][0]
    if interval == "1d":
        path.write_text(json.dumps(raw))
    return raw


def measure(symbol: str, label: str, *, interval: str = "1d") -> Coverage:
    """One instrument's coverage, with the data-quality problems named."""
    out = Coverage(symbol=symbol, label=label)
    try:
        raw = fetch_raw(symbol, interval=interval)
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError) as exc:
        out.error = f"{type(exc).__name__}: {exc}"
        return out

    stamps = raw.get("timestamp") or []
    quote = ((raw.get("indicators") or {}).get("quote") or [{}])[0]
    closes, volumes = quote.get("close") or [], quote.get("volume") or []
    if len(stamps) < 2:
        out.error = "fewer than two bars returned"
        return out

    out.bars = len(stamps)
    out.first = str(datetime.fromtimestamp(stamps[0]).date())
    out.last = str(datetime.fromtimestamp(stamps[-1]).date())
    out.years = (stamps[-1] - stamps[0]) / (365.25 * 86_400)

    gaps = [stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1)]
    out.median_gap_days = round(statistics.median(gaps) / 86_400, 2)
    out.gaps_over_week = sum(1 for g in gaps if g > 7 * 86_400)
    out.duplicate_stamps = len(stamps) - len(set(stamps))
    out.zero_volume_bars = sum(1 for v in volumes if not v)

    if not out.is_daily and interval == "1d":
        out.issues.append(
            f"NOT DAILY — median gap {out.median_gap_days}d, interval downgraded")
    missing = sum(1 for c in closes if c is None)
    if missing:
        out.issues.append(f"{missing} null closes (dropped, never filled)")
    if out.zero_volume_bars > out.bars * 0.05:
        out.issues.append(
            f"{out.zero_volume_bars:,} zero-volume bars — volume filters inert")
    if out.gaps_over_week > 20:
        out.issues.append(f"{out.gaps_over_week} gaps over a week")
    return out


UNIVERSE: list[tuple[str, str]] = [
    ("^GSPC", "S&P 500 (US)"), ("^DJI", "Dow Jones (US)"), ("^IXIC", "Nasdaq (US)"),
    ("^RUT", "Russell 2000 (US)"), ("^N225", "Nikkei (Japan)"),
    ("^FTSE", "FTSE 100 (UK)"), ("^GDAXI", "DAX (Germany)"), ("^FCHI", "CAC 40 (France)"),
    ("^HSI", "Hang Seng (HK)"), ("000001.SS", "Shanghai (China)"),
    ("^BVSP", "Bovespa (Brazil)"), ("^AXJO", "ASX 200 (Australia)"),
    ("^KS11", "KOSPI (Korea)"),
    ("^NSEI", "NIFTY 50 (India)"), ("^NSEBANK", "BANK NIFTY (India)"),
    ("^BSESN", "SENSEX (India)"),
    ("RELIANCE.NS", "Reliance (India)"), ("TCS.NS", "TCS (India)"),
    ("HDFCBANK.NS", "HDFC Bank (India)"), ("INFY.NS", "Infosys (India)"),
    ("GC=F", "Gold futures"), ("CL=F", "Crude futures"), ("SI=F", "Silver futures"),
    ("BTC-USD", "Bitcoin"), ("ETH-USD", "Ethereum"),
    ("USDINR=X", "USD/INR"), ("EURUSD=X", "EUR/USD"),
    ("^VIX", "VIX (volatility)"), ("^TNX", "US 10y yield"),
]


def survey(universe=None) -> list[Coverage]:
    return [measure(sym, label) for sym, label in (universe or UNIVERSE)]


def intraday_ceiling(symbol: str = "RELIANCE.NS") -> dict[str, int]:
    """How far each intraday interval reaches. The binding constraint."""
    out = {}
    for interval in ("1m", "5m", "15m", "30m", "1h"):
        try:
            raw = fetch_raw(symbol, interval=interval)
            out[interval] = len(raw.get("timestamp") or [])
        except Exception:
            out[interval] = 0
    return out

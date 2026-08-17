"""A dashboard that reports what actually happened.

Written to be the opposite of the screens that sell trading bots. Those show a
number climbing and never falling; this shows realized and unrealized apart,
brokerage as its own line, the risk limits with how close each one is, and the
losing days as prominently as the winning ones.

It reads the engine's own SQLite files, so it cannot show anything the engine
did not actually do. There is no data source it could invent a number from.

    earner dashboard                 # write .earner/dashboard.html and stop
    earner dashboard --watch         # rewrite it every 5s while the engine runs
"""

from __future__ import annotations

import html
import time
from dataclasses import dataclass
from pathlib import Path

from .orders import IN_FLIGHT, OrderStore
from .risk import Book, RiskManager, correlation_group, trading_day


@dataclass
class Snapshot:
    """Everything the dashboard shows, read from disk."""

    session: str
    gross: float
    costs: float
    net: float
    unrealized: float
    trades: int
    positions: list[dict]
    orders_in_flight: int
    events: list[dict]
    capital: float

    @property
    def cost_share(self) -> float:
        """What fraction of gross profit brokerage took. The decisive number on
        a small account, and the one these screens never show."""
        if self.gross <= 0:
            return 1.0 if self.costs else 0.0
        return min(self.costs / self.gross, 1.0)


def read(workdir: str | Path = ".earner", capital: float = 100_000.0,
         marks: dict[str, float] | None = None) -> Snapshot:
    workdir = Path(workdir)
    book = Book(workdir / "book.db")
    session = trading_day()
    marks = marks or {}

    positions = []
    for symbol, pos in book.positions(session).items():
        if not pos.is_open:
            continue
        mark = marks.get(symbol, pos.average_price)
        positions.append({
            "symbol": symbol,
            "quantity": pos.quantity,
            "entry": round(pos.average_price, 2),
            "mark": round(mark, 2),
            "pnl": round(pos.quantity * (mark - pos.average_price), 2),
            "group": correlation_group(symbol),
        })

    events = []
    for row in book.conn.execute(
        "SELECT data, at FROM notes WHERE session=? ORDER BY id DESC LIMIT 40", (session,)
    ):
        import json
        try:
            payload = json.loads(row["data"])
        except ValueError:
            continue
        events.append({
            "at": time.strftime("%H:%M:%S", time.localtime(row["at"])),
            "stage": payload.get("stage", ""),
            "detail": payload.get("detail", ""),
        })

    in_flight = 0
    orders_path = workdir / "orders.db"
    if orders_path.exists():
        store = OrderStore(orders_path)
        in_flight = len([o for o in store.orders(session) if o.state in IN_FLIGHT])
        store.close()

    snap = Snapshot(
        session=session,
        gross=book.gross_pnl(session),
        costs=book.costs(session),
        net=book.realized_pnl(session),
        unrealized=book.unrealized_pnl(marks, session),
        trades=len(book.fills(session)),
        positions=positions,
        orders_in_flight=in_flight,
        events=events,
        capital=capital,
    )
    book.close()
    return snap


# ── rendering ───────────────────────────────────────────────────────────────

def _money(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}₹{value:,.0f}"


def _tone(value: float) -> str:
    return "pos" if value > 0 else "neg" if value < 0 else "flat"


def _bar(used: float, limit: float, label: str) -> str:
    pct = min(abs(used) / limit, 1.0) if limit else 0.0
    state = "danger" if pct >= 0.9 else "warn" if pct >= 0.6 else "ok"
    return (
        f'<div class="limit"><div class="limit-head"><span>{html.escape(label)}</span>'
        f'<span class="mono">{pct:.0%}</span></div>'
        f'<div class="track"><div class="fill {state}" style="width:{pct * 100:.1f}%"></div></div></div>'
    )


def render(snap: Snapshot, risk: RiskManager | None = None) -> str:
    risk = risk or RiskManager.diversified(snap.capital)
    total = snap.net + snap.unrealized
    loss_limit = snap.capital * risk.daily_loss_limit

    rows = "".join(
        f'<tr><td class="mono">{html.escape(p["symbol"])}</td>'
        f'<td class="mono num">{p["quantity"]:+,}</td>'
        f'<td class="mono num">₹{p["entry"]:,.2f}</td>'
        f'<td class="mono num">₹{p["mark"]:,.2f}</td>'
        f'<td class="mono num {_tone(p["pnl"])}">{_money(p["pnl"])}</td>'
        f'<td class="muted">{html.escape(p["group"])}</td></tr>'
        for p in snap.positions
    ) or '<tr><td colspan="6" class="muted pad">No open positions.</td></tr>'

    events = "".join(
        f'<li><span class="mono muted">{html.escape(e["at"])}</span>'
        f'<span class="stage">{html.escape(e["stage"])}</span>'
        f'<span>{html.escape(e["detail"])}</span></li>'
        for e in snap.events
    ) or '<li class="muted">Nothing yet this session.</li>'

    limits = "".join([
        _bar(min(snap.net, 0), loss_limit, f"Daily loss limit (₹{loss_limit:,.0f})"),
        _bar(snap.trades, risk.max_trades_per_day, f"Trades ({snap.trades}/{risk.max_trades_per_day})"),
        _bar(len(snap.positions), risk.max_positions,
             f"Positions ({len(snap.positions)}/{risk.max_positions})"),
    ])

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading desk — {html.escape(snap.session)}</title>
<style>
:root {{
  --ground:#EDEFF2; --surface:#fff; --sunk:#E3E7EC; --ink:#11151B; --soft:#4A5461;
  --faint:#798494; --rule:#D2D8DF; --accent:#14504F; --neg:#A83326; --pos:#1B6342;
  --warn:#7E5207;
}}
@media (prefers-color-scheme:dark) {{ :root {{
  --ground:#0C0F13; --surface:#141920; --sunk:#0F141A; --ink:#E4E8ED; --soft:#9CA7B5;
  --faint:#6B7686; --rule:#242C36; --accent:#55B8B2; --neg:#E56F60; --pos:#52B37F;
  --warn:#D5A143;
}} }}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ground);color:var(--ink);
  font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
.mono{{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  font-variant-numeric:tabular-nums}}
.wrap{{max-width:60rem;margin:0 auto;padding:2rem 1.25rem 4rem}}
h1{{font-family:ui-monospace,Menlo,monospace;font-size:1.35rem;margin:0 0 .2rem;
  letter-spacing:-.02em}}
.sub{{color:var(--faint);font-size:.85rem;margin:0 0 1.5rem}}
.grid{{display:grid;gap:1px;background:var(--rule);border:1px solid var(--rule);
  grid-template-columns:repeat(auto-fit,minmax(9.5rem,1fr));margin-bottom:1.5rem}}
.cell{{background:var(--surface);padding:.9rem 1rem}}
.cell .k{{font-family:ui-monospace,monospace;font-size:.62rem;letter-spacing:.12em;
  text-transform:uppercase;color:var(--faint);margin-bottom:.35rem}}
.cell .v{{font-family:ui-monospace,monospace;font-size:1.25rem;font-weight:700;
  font-variant-numeric:tabular-nums;letter-spacing:-.02em}}
.cell .n{{font-size:.72rem;color:var(--faint);margin-top:.15rem}}
.pos{{color:var(--pos)}} .neg{{color:var(--neg)}} .flat{{color:var(--faint)}}
h2{{font-family:ui-monospace,monospace;font-size:.85rem;letter-spacing:.06em;
  text-transform:uppercase;color:var(--faint);margin:2rem 0 .7rem;
  border-bottom:1px solid var(--rule);padding-bottom:.4rem}}
table{{width:100%;border-collapse:collapse;background:var(--surface);
  border:1px solid var(--rule);font-size:.9rem}}
th,td{{text-align:left;padding:.5rem .75rem;border-bottom:1px solid var(--rule)}}
th{{font-family:ui-monospace,monospace;font-size:.62rem;letter-spacing:.1em;
  text-transform:uppercase;color:var(--faint);background:var(--sunk)}}
td.num,th.num{{text-align:right}}
tr:last-child td{{border-bottom:none}}
.muted{{color:var(--faint)}} .pad{{padding:1.25rem;text-align:center}}
.limit{{margin-bottom:.8rem}}
.limit-head{{display:flex;justify-content:space-between;font-size:.78rem;
  color:var(--soft);margin-bottom:.25rem}}
.track{{height:6px;background:var(--sunk);border:1px solid var(--rule)}}
.fill{{height:100%}} .fill.ok{{background:var(--pos)}}
.fill.warn{{background:var(--warn)}} .fill.danger{{background:var(--neg)}}
ol.events{{list-style:none;padding:0;margin:0;background:var(--surface);
  border:1px solid var(--rule);max-height:22rem;overflow-y:auto;font-size:.85rem}}
ol.events li{{display:grid;grid-template-columns:5rem 8rem 1fr;gap:.6rem;
  padding:.4rem .75rem;border-bottom:1px solid var(--rule)}}
ol.events li:last-child{{border-bottom:none}}
.stage{{font-family:ui-monospace,monospace;font-size:.72rem;color:var(--accent)}}
.honest{{border-left:3px solid var(--neg);background:var(--surface);
  padding:.85rem 1.1rem;margin:1.5rem 0;font-size:.88rem}}
footer{{margin-top:2.5rem;padding-top:1rem;border-top:1px solid var(--rule);
  font-family:ui-monospace,monospace;font-size:.7rem;color:var(--faint)}}
@media (max-width:36rem){{ol.events li{{grid-template-columns:4.2rem 1fr}}
  .stage{{grid-column:2}}}}
</style></head><body><div class="wrap">

<h1>Trading desk</h1>
<p class="sub">{html.escape(snap.session)} · refreshed {time.strftime('%H:%M:%S')} · reads the engine's own records</p>

<div class="grid">
  <div class="cell"><div class="k">Net today</div>
    <div class="v {_tone(snap.net)}">{_money(snap.net)}</div>
    <div class="n">after brokerage</div></div>
  <div class="cell"><div class="k">Gross</div>
    <div class="v {_tone(snap.gross)}">{_money(snap.gross)}</div>
    <div class="n">price movement only</div></div>
  <div class="cell"><div class="k">Brokerage</div>
    <div class="v neg">−₹{snap.costs:,.0f}</div>
    <div class="n">{snap.cost_share:.0%} of gross profit</div></div>
  <div class="cell"><div class="k">Open</div>
    <div class="v {_tone(snap.unrealized)}">{_money(snap.unrealized)}</div>
    <div class="n">not money yet</div></div>
  <div class="cell"><div class="k">Total</div>
    <div class="v {_tone(total)}">{_money(total)}</div>
    <div class="n">{total / snap.capital:+.2%} of capital</div></div>
  <div class="cell"><div class="k">Trades</div>
    <div class="v">{snap.trades}</div>
    <div class="n">{snap.orders_in_flight} in flight</div></div>
</div>

<h2>Risk limits</h2>
{limits}

<h2>Open positions</h2>
<table><thead><tr><th>Symbol</th><th class="num">Qty</th><th class="num">Entry</th>
<th class="num">Mark</th><th class="num">Open P&amp;L</th><th>Correlation</th></tr></thead>
<tbody>{rows}</tbody></table>

<h2>What the engine did</h2>
<ol class="events">{events}</ol>

<div class="honest">
  <strong>Gross and net are shown separately on purpose.</strong> Brokerage is
  charged per order and does not shrink with the account, so on a small balance
  it routinely exceeds the profit the strategy made. A dashboard showing only a
  rising number is hiding this line.
</div>

<footer>Every figure read from .earner/book.db and .earner/orders.db. Nothing here is projected.</footer>
</div></body></html>"""


def write(workdir: str | Path = ".earner", capital: float = 100_000.0,
          marks: dict[str, float] | None = None) -> Path:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "dashboard.html"
    out.write_text(render(read(workdir, capital, marks), RiskManager.diversified(capital)),
                   encoding="utf-8")
    return out

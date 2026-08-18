"""One command that produces every number in the rapid-engine report.

It exists so that the report and the code cannot drift apart. Every figure in
`docs/RAPID_ENGINE.md` comes out of this function; if a number there is wrong,
this is the only place it can be wrong.

It is slow and it fetches real data, so it is not part of the test suite. The
test suite proves the machinery is correct; this proves what the machinery
finds, which is a different question and cannot be answered by an assertion.
"""

from __future__ import annotations

import sys

from earner.trading.marketdata import load_universe
from quant_os.features.flow import agreement
from quant_os.features.regime import bucket
from quant_os.rapid.ablation import study, table
from quant_os.rapid.backtest import run as portfolio
from quant_os.rapid.labeling import build_dataset
from quant_os.rapid.scorecard import score
from quant_os.rapid.walkforward import run as walk

UNIVERSE = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "ITC",
            "LT", "AXISBANK", "SUNPHARMA", "BHARTIARTL", "KOTAKBANK",
            "MARUTI", "TATASTEEL", "HINDUNILVR"]


def _group(rows, key):
    out = {}
    for row in rows:
        out.setdefault(key(row), []).append(row)
    return out


def _line(name, rows):
    n = len(rows)
    if not n:
        return f"{name:<22}{0:>6}"
    return (f"{name:<22}{n:>6}{sum(o.win for o in rows) / n:>8.1%}"
            f"{sum(o.gross_return for o in rows) / n:>11.4%}"
            f"{sum(o.net_return for o in rows) / n:>11.4%}"
            f"{sum(o.mfe for o in rows) / n:>9.3%}{sum(o.mae for o in rows) / n:>9.3%}")


def main(interval: str = "5m", days: int = 60, capital: float = 100_000.0,
         out=sys.stdout) -> int:
    bar_minutes = int(interval.rstrip("m")) if interval.endswith("m") else 5
    print(f"\n{'=' * 78}\nRAPID ENGINE STUDY  —  {interval} bars, {days} days, "
          f"{len(UNIVERSE)} NSE symbols\n{'=' * 78}", file=out)

    data = load_universe(UNIVERSE, interval=interval, days=days)
    if not data:
        print("no data", file=out)
        return 1
    sessions = sorted({d for s in data.values() for d in s})
    print(f"\n{len(data)} symbols, {len(sessions)} sessions, "
          f"{sessions[0]} to {sessions[-1]}", file=out)

    print("\n-- 1. ORDER-FLOW PROXY QUALITY " + "-" * 47, file=out)
    print("   Two independent direction estimators, per symbol. Where they", file=out)
    print("   disagree, the bar's direction is not recoverable from OHLCV.", file=out)
    for symbol, days_map in sorted(data.items()):
        bars = [c for d in sorted(days_map) for c in days_map[d]]
        print(f"   {symbol:<12}{len(bars):>7} bars   agreement {agreement(bars):>6.1%}", file=out)

    print("\n-- 2. RAW SETUP PERFORMANCE " + "-" * 50, file=out)
    outcomes = build_dataset(data, bar_minutes=bar_minutes)
    if not outcomes:
        print("   no setups detected", file=out)
        return 1
    header = f"{'setup':<22}{'n':>6}{'win%':>8}{'gross':>11}{'net':>11}{'MFE':>9}{'MAE':>9}"
    print(header, file=out)
    for kind, rows in sorted(_group(outcomes, lambda o: o.kind).items(),
                             key=lambda p: -len(p[1])):
        print(_line(kind, rows), file=out)
    print(_line("ALL", outcomes), file=out)

    print("\n-- 3. BY SESSION BUCKET " + "-" * 54, file=out)
    print(header, file=out)
    for name in ("OPEN", "EARLY", "MID", "AFTERNOON", "CLOSE"):
        rows = _group(outcomes, lambda o: bucket(o.at)).get(name, [])
        if rows:
            print(_line(name, rows), file=out)

    print("\n-- 4. BY REGIME " + "-" * 62, file=out)
    print(header, file=out)
    for state, rows in sorted(_group(outcomes, lambda o: o.regime).items(),
                              key=lambda p: -len(p[1])):
        if len(rows) >= 20:
            print(_line(state, rows), file=out)

    print("\n-- 5. WALK-FORWARD MODEL QUALITY " + "-" * 45, file=out)
    for mode in ("anchored", "rolling"):
        result = walk(outcomes, folds=6, mode=mode)
        pooled = result.pooled()
        if not pooled:
            continue
        print(f"   {mode:<10} folds {len(result.folds)}  n {pooled['n']}  "
              f"base {pooled['base_rate']:.1%}  AUC {pooled['auc']:.3f}  "
              f"Brier {pooled['brier']:.4f} (base {pooled['brier_base']:.4f})", file=out)
        if mode == "anchored":
            anchored = result

    print("\n   Per-head out-of-sample AUC, by fold:", file=out)
    for head in ("PRICE", "FLOW", "MOMENTUM", "REVERSAL", "REGIME", "VOLATILITY"):
        values = [f.metrics[f"auc_{head}"] for f in anchored.folds]
        mean = sum(values) / len(values) if values else 0.0
        print(f"   {head:<12} mean {mean:.3f}   {[round(v, 2) for v in values]}", file=out)

    print("\n-- 6. SELECTIVITY CURVE " + "-" * 54, file=out)
    print("   If the model ranks anything, mean net return rises with the", file=out)
    print("   threshold. A flat curve means the ranking is noise.", file=out)
    print(f"   {'threshold':>10}{'trades':>8}{'win%':>8}{'mean gross':>12}{'mean net':>11}",
          file=out)
    pairs = anchored.predictions
    probabilities = sorted(p for p, _ in pairs)
    for i in range(10):
        threshold = probabilities[int(i / 10 * (len(probabilities) - 1))]
        kept = [(p, o) for p, o in pairs if p >= threshold]
        if len(kept) < 20:
            continue
        print(f"   {threshold:>10.3f}{len(kept):>8}"
              f"{sum(o.win for _, o in kept) / len(kept):>8.1%}"
              f"{sum(o.gross_return for _, o in kept) / len(kept):>12.4%}"
              f"{sum(o.net_return for _, o in kept) / len(kept):>11.4%}", file=out)

    print("\n-- 7. PORTFOLIO BACKTEST, FULL PIPELINE " + "-" * 38, file=out)
    result = portfolio(outcomes, capital=capital)
    print(f"   candidates {result.considered}   traded {len(result.trades)}   "
          f"blocked by concurrency {result.blocked_full}   "
          f"model refits {result.train_blocks}", file=out)
    print("\n   Why candidates were declined:", file=out)
    for reason, count in sorted(result.rejected.items(), key=lambda p: -p[1])[:10]:
        print(f"   {count:>7}  {reason}", file=out)
    if result.trades:
        base = sum(o.win for o in outcomes) / len(outcomes)
        card = score(result.trades, capital=capital, base_rate=base,
                     daily=[n for _, n in result.daily])
        print("", file=out)
        print(card.report(), file=out)
        print(f"\n   equity: {capital:,.0f} -> {result.equity_end:,.0f}", file=out)

    print("\n-- 8. ABLATION " + "-" * 63, file=out)
    print(table(study(outcomes, capital=capital)), file=out)
    print("", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

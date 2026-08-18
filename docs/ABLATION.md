# Feature ablation — what actually contributes

Out-of-sample: 29 held-out NSE sessions, 10 symbols, 5-minute bars. Components
added one at a time to a stripped baseline.

| Configuration | Trades | Gross | Cost | **Net** | Per trade | Δ |
|---|---:|---:|---:|---:|---:|---:|
| BASELINE (all gates off) | 2,444 | −3,108 | 53,125 | **−56,233** | −23.0 | — |
| + regime filter | 466 | −710 | 10,091 | **−10,801** | −23.2 | **+45,433** |
| + EV gate | 466 | −710 | 10,091 | **−10,801** | −23.2 | **+0** |
| + entry-drift gate | 466 | −710 | 10,091 | **−10,801** | −23.2 | **+0** |
| + risk engine | 414 | −8,640 | 18,477 | **−27,117** | −65.5 | **−16,316** |
| + all 3 strategies | 654 | −11,436 | 29,144 | **−40,580** | −62.0 | **−13,463** |

## What each component actually does

**Regime filter: +₹45,433, and none of it is alpha.** The headline improvement
is the largest in the table, and it is entirely a trade-count effect: 2,444
trades become 466. Per-trade economics are **unchanged** (−23.0 → −23.2). The
filter is not selecting better trades; it is selecting fewer. That distinction
is invisible in the net column and decisive for whether it generalises.

**EV gate: exactly zero. It has never fired.** Instrumented over 107 signals:
**0 blocked**. `VWAPMomentum` emits a fixed 1.8:1 reward-to-risk at 50–70%
confidence, so expected value is positive by construction and the gate cannot
bind for the strategy it guards.

**Entry-drift gate: exactly zero, and it cannot fire in a backtest at all.**
The signal's entry price *is* `candles[-1].close` and the mark *is*
`candles[-1].close`, so measured drift is identically 0. This gate only has
meaning live, where price moves between decision and fill — meaning the
backtest does not test it and never has.

**Risk engine: −₹16,316.** It makes things worse here, and the reason is
instructive rather than alarming. Risk-based sizing produces *larger* positions
than the unconstrained default, so on a negative edge it faithfully amplifies
the loss. Per trade falls from −23.2 to −65.5. The engine is doing its job
correctly; there is simply nothing worth sizing up.

**Extra strategies: −₹13,463.** Adding opening-range breakout and mean
reversion to VWAP momentum makes the result worse. They are not diversifying;
they are adding correlated losing trades.

## The diagnosis

Every configuration is negative, and the gross line is negative in all of them.
No arrangement of these components produces a positive expectancy, because the
components are filters and a filter cannot create an edge that is not present.

The mechanical cause is measured separately: **the intraday session has
negative drift.** NIFTY returns +23.7%/yr overnight and −12.4%/yr intraday;
RELIANCE +53.7% against −19.3%. A bot flat overnight and positioned intraday
holds only the losing half of the day.

## Actions this implies

1. Two gates are dead code in practice. The EV gate should be either removed or
   given a strategy whose EV it can actually constrain; the drift gate needs a
   backtest that models fill lag or it is untested.
2. The regime filter's benefit should be reported as reduced turnover, not as
   improved selection, or it will be trusted for the wrong reason.
3. Adding components to this system will not help. The deficit is directional.

Reproduce: see `reports/` and the ablation script in the commit that added this file.

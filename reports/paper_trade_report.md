# Paper trading report

Five real NSE sessions replayed bar by bar through the production path — the
same `Engine`, `RiskManager`, `OrderStore`, reconciliation and cost model that
live trading uses. Only the broker adapter differs.

**This is a replay of the last five sessions, not five days of forward paper
trading.** Forward paper trading takes five calendar days and cannot be
compressed. What this does give is the identical code path against the prices
those sessions actually traded at.

## Result

| Session | Trades | Gross | Brokerage | Net | Halted |
|---|---:|---:|---:|---:|---|
| 2026-08-11 | 10 | −528 | 444 | **−972** | yes |
| 2026-08-12 | 18 | −1,550 | 788 | **−2,338** | yes |
| 2026-08-13 | 12 | −710 | 537 | **−1,247** | yes |
| 2026-08-14 | 26 | −363 | 1,163 | **−1,526** | yes |
| 2026-08-17 | 16 | +335 | 714 | **−379** | yes |
| **Week** | **82** | **−2,816** | **3,646** | **−6,462** | |

₹1,00,000 capital · `diversified` preset · **−6.46%** · **0 of 5 winning days**

## What worked

Everything mechanical. Reconciliation was clean on all five sessions; the order
store, write-ahead journal, daily loss limit, square-off and duplicate
protection all behaved as specified. The engine halted itself every single day
on the daily loss limit, which is the risk layer doing exactly its job.

## What did not

The strategies. Gross P&L before any brokerage was **−₹2,816** — they lose on
price movement alone. On the one day price movement was positive (+₹335),
brokerage of ₹714 turned it into a loss anyway.

## The finding

Brokerage was **₹3,646 on ₹1,00,000 in five days** — 0.73% per day, roughly
182% annualised.

The best peer-reviewed A-share quant result available
([arXiv 2506.06356](https://arxiv.org/html/2506.06356v1): 15.2% annualised,
Sharpe 1.87, 4.8% max drawdown) runs an all-in cost of **22.1 basis points per
year**. That is 0.221%.

**Our cost drag is roughly 825× theirs.** Not because their broker is cheaper —
because they hold positions for up to nine days and turn over ~21× a year,
while this system holds for minutes and turns over ~4,100× a year.

Their gross edge only has to beat 0.22% a year. Ours has to beat 182%. Nothing
does that, which is why no amount of strategy improvement rescues an intraday
system at this capital.

## Conclusion

No promotion. The strategies stay in RESEARCH. Nothing here justifies live
capital, and the daily loss limit halting on five days out of five is the
system correctly refusing to keep losing.

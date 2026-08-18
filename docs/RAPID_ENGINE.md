# India rapid trading / aggressive alpha engine

Built to the brief: microstructure, order flow, order book, machine learning,
rapid execution, aggressive allocation, strict risk. Thirteen deliverables,
below, in the order requested.

Every number in this document is produced by one command and nothing else:

```
python -m quant_os.rapid.study            # or: earner rapid --interval 5m --days 60
```

If a figure here disagrees with that command's output, the command is right.

**Live trading is disabled and must stay disabled.** Nothing in this document
clears the validation pipeline. The measured result is that the engine, fully
assembled, declines almost every trade it is offered — and that this is the
correct behaviour given what the data shows.

---

## 1. Existing bot audit

The full audit is `docs/CODEBASE_AUDIT.md`. What mattered for this build:

| Area | State before | What this work changed |
|---|---|---|
| Signals | Three rule strategies (ORB, VWAP momentum, mean reversion) on RSI/ATR/EMA | Five microstructure setup detectors, none of which use an oscillator |
| Costs | One blended rate: 0.05% turnover + 3bp slippage | Itemised statutory model per segment, plus estimated spread and square-root impact |
| Direction | Not modelled | Two independent bar-level estimators, with their disagreement measured and carried as a feature |
| Decision | Fixed confidence thresholds | Ensemble probability → expected value → adversarial check → tier → size |
| Sizing | Fixed fraction, per-position margin cap | Tiered by setup quality, tapered by drawdown, capped by participation |
| Validation | Single chronological split | Purged walk-forward, anchored and rolling, refit per fold |
| Order book | `quant_os/data/depth.py` (live only, no history) | Unchanged — and this is the binding constraint on the whole brief |

Three findings from the audit that this build had to design around, all of them
measured rather than assumed:

1. **The intraday session has negative drift.** NIFTY returns +23.7%/yr
   overnight and −12.4%/yr inside the session; RELIANCE +53.7% vs −19.3%;
   negative in 8 of 13 instruments tested. An intraday long-biased strategy is
   swimming against a real current.
2. **Intraday strategies lost gross, not just net** — −₹1,574 at zero
   brokerage and zero slippage across 58 real NSE days. Cost was never the
   binding problem.
3. **The feed has no tape and no book.** `open, high, low, close, volume`, and
   nothing else. Every order-flow number in this system is therefore an
   estimate, and section 4 below measures how good an estimate.

## 2. GitHub research

Searched and studied; see `docs/GITHUB_RESEARCH.md` for the earlier round.
This round targeted limit-order-book ML and microstructure specifically.

| Project | What was taken | What was rejected, and why |
|---|---|---|
| [Jeonghwan-Cheon/lob-deep-learning](https://github.com/Jeonghwan-Cheon/lob-deep-learning) — DeepLOB, TransLOB, DeepFolio | The input representation: ten levels × (price, size) × both sides, as a fixed tensor. `quant_os/data/depth.py` already produces exactly this shape. | The models. DeepLOB is a CNN-LSTM over **tick-level book snapshots**; there is no book history here to train one on, and training a 100k-parameter network on 3,500 bar-level rows would fit noise perfectly. |
| [sadighian/crypto-rl](https://github.com/sadighian/crypto-rl) | The architecture insight that matters most: it is fundamentally a **recorder** first and an agent second. Book data cannot be bought retrospectively, so recording precedes research. This is why the depth subscriber was built before any of this. | The DDQN agent. Reinforcement learning needs orders of magnitude more episodes than an Indian equity session provides, and its reward function is exactly where look-ahead hides. |
| [ajcutuli/OFI_NN_Project](https://github.com/ajcutuli/OFI_NN_Project) | Order-flow imbalance as the *primary* predictor rather than a confirmation filter — the finding that OFI carries more short-horizon information than price does. Implemented as the FLOW head, which is given equal standing with PRICE. | Its neural specification, for the sample-size reason above. |
| [AKNavin/HFT-Order-Analysis-NSE](https://github.com/AKNavin/HFT-Order-Analysis-NSE) | The only Indian-market LOB study found. Confirms NSE Level-2 is obtainable and that limit-order lambda / OFI are computable on it. | Nothing to adapt — it is an analysis notebook, not a strategy, and makes no profitability claim. |
| [BlackArbsCEO/Adv_Fin_ML_Exercises](https://github.com/BlackArbsCEO/Adv_Fin_ML_Exercises) (1.9k★) and the López de Prado family | Triple-barrier labelling (stop / target / time limit) is exactly what `quant_os/rapid/labeling.py` implements; purging and embargo are in `walkforward.py`. | Meta-labelling as a second stage. It is the right next step and needs a primary model with demonstrated skill to sit on top of. This one has none (§10). |
| [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) (26k★) | Research-to-live semantic parity: the same decision code in backtest and live. `decide()` returns a `Decision`, never an order, so both callers share it. | The engine itself — a Rust dependency against a stack that deliberately has none. |

**An observation worth recording.** Searching GitHub for Indian intraday
trading bots returns, at the top: a data API (132★), then repositories with
3, 1, 1, 0, 0 and 0 stars. There is no credible public implementation of a
profitable NSE intraday system. Compare limit-order-book ML, where the serious
work sits at 100–1,000★ and is uniformly *research*, publishing accuracy
metrics rather than P&L. The absence is itself evidence about the problem.

## 3. New architecture

```
        market data (OHLCV, 1m/5m)          [ Level-2: live recorder only ]
                   │                                      │
                   ▼                                      ▼
   quant_os/features/flow.py                 quant_os/data/depth.py
   order-flow proxies + estimator            microprice, imbalance,
   agreement diagnostic                      slippage-to-fill
                   │                                      │
                   ▼                                      ✗ no history to train on
   quant_os/features/regime.py
   10 regime states + 5 session buckets
                   │
                   ▼
   quant_os/rapid/setups.py
   5 detectors → Setup + feature vector      (recall, not precision)
                   │
                   ▼
   quant_os/rapid/labeling.py
   triple-barrier outcome, costs charged,    ← the only place lookahead can enter
   entry at next bar's open
                   │
                   ▼
   quant_os/models/ensemble.py
   PRICE · FLOW · MOMENTUM · REVERSAL ·      BOOK head present but empty
   REGIME · VOLATILITY  →  stacker
   + ridge heads: E[return], E[MAE], E[MFE]
                   │
                   ▼
   quant_os/rapid/decide.py
   expected value → adversarial check →      ← arithmetic, never the model,
   tier (A+/A/B/C) → size                       authorises a trade
                   │
                   ▼
   quant_os/rapid/backtest.py                quant_os/rapid/walkforward.py
   portfolio, concurrency, drawdown taper    purged, embargoed, refit per fold
                   │
                   ▼
   quant_os/rapid/scorecard.py + ablation.py
```

The architectural rule that shapes all of it: **the model proposes, arithmetic
disposes.** A probability is an input to `expected_value()`, never a permission.
No learned component can authorise an order — the only thing that can is a
positive number coming out of a cost calculation whose inputs were looked up.

## 4. Features added

`quant_os/features/flow.py` — order-flow proxies from bars:

- close location value; buy/sell volume split
- signed volume (tick rule at bar resolution); cumulative delta, two ways
- volume z-score, volume acceleration, range z-score
- absorption (heavy volume, narrow range), exhaustion (heavy volume, wide
  range, weak close)
- aggressive-burst flag, trade-size proxy, flow/price divergence
- **estimator agreement** — the diagnostic that says how much to trust the rest

`quant_os/features/regime.py` — ten regime states, five session buckets,
minutes-into-session and minutes-to-close.

`quant_os/execution/costs.py` — Corwin–Schultz effective spread from
high/low ranges, and square-root market impact.

`quant_os/rapid/setups.py` — price acceleration (second derivative in units of
the instrument's own volatility), extension beyond a broken level in ATR,
retracement fraction, impulse-to-pullback volume ratio, stretch in sigma.

### The honesty constraint on all of it

The brief asks for aggressive buy volume, cumulative delta and trade-size
distribution. Those are defined on the trade tape. This feed has no tape. What
is computed instead are estimators of the direction split from bar shape, and
the module measures its own reliability by running two independent estimators
and reporting how often they agree:

```
   Two independent direction estimators, 15 NSE symbols, 64,472 5-minute bars

   TCS        81.4%      RELIANCE   79.9%      BHARTIARTL 79.8%
   ICICIBANK  79.7%      MARUTI     79.5%      SBIN       79.3%
   LT         79.3%      AXISBANK   79.2%      HDFCBANK   79.2%
   TATASTEEL  79.2%      SUNPHARMA  78.9%      INFY       78.8%
   KOTAKBANK  78.6%      HINDUNILVR 78.1%      ITC        74.5%
```

Agreement between two proxies is not accuracy against a tape — it is an
upper bound on how much of the direction signal is even recoverable.

That is consistent with the bulk-volume-classification literature (Easley,
López de Prado & O'Hara measured 70–80% on liquid futures). It also means
roughly one bar in five has a direction this system cannot resolve. **A signal
that needs the missing fifth is not available from this data.**

## 5. Models added

`quant_os/models/logistic.py`

- `Logistic` — L2-regularised logistic regression fitted by IRLS (Newton).
  Converges in 6–8 iterations; the first implementation used 400 epochs of
  gradient descent and made the walk-forward too slow to run, and a validation
  step that is too slow is a validation step that stops being run.
- `Ridge` — L2 least squares by normal equations, for the continuous heads.
- `solve` — Gaussian elimination with partial pivoting. Pivoting is not
  optional: one-hot regime columns are all-zero in most folds.
- `calibration`, `brier`, `auc` — because an uncalibrated probability breaks
  the expected-value gate that multiplies by it.

Capacity was chosen deliberately. 3,500 rows and ~25 features is not a
gradient-boosting problem; it is a problem where a hundred-tree model fits the
sample beautifully and generalises to nothing.

`quant_os/models/ensemble.py` — six active heads on disjoint feature groups,
combined by a stacker trained on **out-of-fold** head predictions, plus three
ridge heads for expected return, expected adverse excursion and expected
favourable excursion.

**The BOOK head is empty and stays empty.** There is no historical Level-2
data. A stub returning 0.5 would let a reader believe the book was being used;
naming it and leaving it untrained states the largest gap in the system in the
one place nobody can miss it.

## 6. Strategy modules added

| Setup | Confluence required | Horizon |
|---|---|---|
| `momentum_burst` | volume z > 1.5, flow imbalance > 0.35, price acceleration > 0.5σ, range expansion, **and flow and acceleration agreeing on direction** | 15 min |
| `breakout` | close beyond a range that *excludes the last three bars*, volume confirmation, flow direction, close in the top/bottom 40% of its bar, and extension < 2 ATR | 30 min |
| `breakout_failure` | a break in the last four bars, a close back inside, flow reversed | 20 min |
| `trend_continuation` | EMA structure, retracement between 20% and 60% of the impulse, pullback volume below impulse volume, delta still one-sided | 45 min |
| `rapid_reversal` | move > 2σ over five bars, exhaustion signature, volume expansion, and a close rejected from the extreme | 20 min |

Two design decisions worth stating:

- **The range a breakout breaks excludes the breakout bar and the two before
  it.** Including them is the classic same-bar lookahead in breakout code, and
  it is invisible in the output.
- **Horizon is a property of the setup, not a parameter.** §2 asks the system
  to select the holding period automatically; it does, by kind.

## 7. Risk engine

`quant_os/rapid/decide.py`:

| Tier | Required edge (× round-trip cost) | Required probability | Max head dispersion | Capital risked |
|---|---|---|---|---|
| A+ | 3.0× | ≥ 0.55 | < 0.06 | 1.0% |
| A | 2.0× | ≥ 0.45 | < 0.09 | 0.6% |
| B | 1.5× | ≥ 0.40 | — | 0.3% |
| C | — | — | — | **no trade** |

C is in the table on purpose: the no-trade case is a tier, so it cannot be
omitted by accident.

`size_for()` has one invariant — **every clause reduces size; none increases
it.** The drawdown taper halves size at a 10% drawdown and zeroes it at 20%.
Nothing in the function can respond to a loss by trading larger, which is the
martingale §10 forbids, enforced structurally rather than by a comment. There
is a test that walks the drawdown from 0% to 20% and asserts the size sequence
is monotonically non-increasing.

The adversarial check (§19) is nine questions, each answered from a feature
computed before the trade — never from a judgement. A checklist whose items are
answered by opinion always passes.

## 8. Execution engine

`quant_os/execution/costs.py` charges, per leg:

- brokerage: 0.03% of turnover, **capped at ₹20** — so cost per share *falls*
  with trade size, which a flat-percentage model gets badly wrong in the
  direction that manufactures edges
- STT: 0.025% (equity intraday), **sell leg only**
- exchange transaction charge, SEBI turnover fee, NSE IPFT
- stamp duty: 0.003%, **buy leg only**
- GST: 18% on brokerage + exchange + SEBI

Separately, because they are estimates rather than known amounts: Corwin–Schultz
half-spread (floored at 1bp per side, since a zero-cost fill is the single most
common way a backtest invents an edge) and square-root impact at coefficient
0.6 — the middle of the published range, deliberately not tuned.

Segments implemented: equity intraday, index futures, index options. Options
cost roughly an order of magnitude more per rupee of premium turnover, which is
why option scalping needs a far larger move than it appears to.

Execution realism in the backtest: entry at the **next** bar's open, never the
detection close; a bar touching both stop and target resolves as the **stop**;
fills capped at 10% of bar volume (partial fills); positions squared off before
15:20.

## 9. Backtesting results

**Universe:** 15 NSE large-caps. **Sample:** 58 sessions, 2026-05-27 to
2026-08-18, 5-minute bars, 64,472 bars. **Capital:** ₹1,00,000.

### Every setup, before any model or gate

5,233 candidates, labelled with entry at the next bar's open and a bar that
touches both stop and target resolved as a stop.

| Setup | n | Win % | Mean gross | Mean net | MFE | MAE |
|---|---:|---:|---:|---:|---:|---:|
| `breakout` | 1,590 | 26.1% | −0.0512% | −0.1240% | 0.159% | 0.198% |
| `trend_continuation` | 1,335 | 32.3% | −0.0580% | −0.1327% | 0.231% | 0.270% |
| `momentum_burst` | 1,262 | 22.6% | −0.0763% | −0.1493% | 0.134% | 0.183% |
| `breakout_failure` | 992 | 25.5% | −0.0451% | −0.1172% | 0.154% | 0.177% |
| `rapid_reversal` | 54 | 20.4% | −0.1870% | −0.2613% | 0.188% | 0.370% |
| **All** | **5,233** | **26.7%** | **−0.0592%** | **−0.1325%** | 0.171% | 0.211% |

**Every setup loses gross.** Cost drag is 7.3bp per trade; gross return is
−5.9bp. Removing every rupee of brokerage, tax and slippage still leaves a
losing strategy. This reproduces, with a far more sophisticated engine, the
finding already recorded in the audit — and it is worth being clear that a
better cost model, a better broker or a lower-latency connection would not
change it.

### By session bucket (§12)

| Bucket | n | Win % | Mean gross | Mean net |
|---|---:|---:|---:|---:|
| EARLY | 85 | 28.2% | −0.0737% | −0.1452% |
| MID | 2,043 | 29.1% | −0.0460% | −0.1195% |
| AFTERNOON | 1,838 | 29.7% | −0.0398% | −0.1131% |
| CLOSE | 1,267 | 18.2% | **−0.1078%** | −0.1805% |

The OPEN bucket is empty and that is structural: a 25-bar warm-up on 5-minute
data is 125 minutes, so the open is unreachable at this resolution. It is
reachable at 1-minute, where it has 20 observations — not a sample.

**The strongest single finding in the whole study is negative and is here.**
The closing bucket is roughly twice as bad as the rest of the day, on 1,267
observations. §12 says "do not assume the opening session is always
profitable"; the measured answer is that the *closing* session is reliably
worse, presumably because square-off flow makes the last half hour a period
where everyone is a forced seller of their own position.

### By regime (§13)

| Regime | n | Win % | Mean gross | Mean net |
|---|---:|---:|---:|---:|
| RANGE | 2,851 | 26.8% | −0.0540% | −0.1270% |
| WEAK_TREND | 989 | 25.1% | −0.0572% | −0.1301% |
| EXHAUSTION | 355 | 25.6% | −0.0513% | −0.1248% |
| LOW_VOLATILITY | 333 | 34.5% | −0.0260% | −0.1021% |
| REVERSAL | 324 | 27.2% | −0.0513% | −0.1250% |
| HIGH_VOLATILITY | 232 | 20.3% | **−0.1928%** | −0.2651% |
| STRONG_TREND | 73 | 30.1% | −0.0469% | −0.1197% |
| BREAKOUT | 67 | 25.4% | −0.0922% | −0.1689% |

The second real finding, also negative: **high volatility is four times worse
than the rest**, on 232 observations. It is the regime a rapid-trading system
is most tempted into, and it is the one that punishes hardest. The regime
classifier's genuine value in this data is as a veto, not as a strategy
selector.

### The full pipeline, walked forward

| | |
|---|---|
| Candidates offered | 3,295 |
| **Trades taken** | **1** |
| Blocked by concurrency | 0 |
| Model refits | 8 |
| Net P&L | **−₹223** |
| Equity | ₹1,00,000 → ₹99,777 |

Why the other 3,294 were declined (a candidate can fail several checks):

| Count | Reason |
|---:|---|
| 3,273 | edge multiple below the tier-B threshold |
| 3,256 | the return model expects a loss |
| 3,245 | expected value negative after costs |
| 620 | not enough session left to hold the intended horizon |
| 463 | flow proxy points against the trade |
| 397 | volume below its recent mean |
| 170 | exhaustion signature on a continuation setup |
| 140 | entry more than 1.5 ATR beyond the level |
| 26 | direction estimators agree on < 60% of recent bars |
| 16 | heads disagree (dispersion > 0.12) |

**One trade in 3,295 is the result, and it is the correct one.** The engine
was asked to trade only where probability-adjusted expected value is
sufficiently positive after all costs. On this data that condition is
essentially never met, so it essentially never trades. A version of this
system that took a hundred trades here would be a version whose gates had been
loosened until they passed — which is the failure mode the whole architecture
exists to prevent.

### Ultra-short: 1-minute bars (§2)

The brief asks for a seconds-to-minutes tier. One minute is the finest
resolution the feed provides, and it reaches back seven days — 14 symbols,
2,572 labelled setups.

| Setup | n | Win % | Mean gross | Mean net |
|---|---:|---:|---:|---:|
| `breakout` | 928 | 19.1% | −0.0781% | −0.1531% |
| `momentum_burst` | 846 | 17.1% | −0.0477% | −0.1215% |
| `breakout_failure` | 611 | 23.1% | −0.0954% | −0.1665% |
| `trend_continuation` | 159 | 34.0% | −0.0326% | −0.1056% |
| `rapid_reversal` | 28 | 17.9% | −0.0625% | −0.1389% |
| **All** | **2,572** | **20.3%** | **−0.0692%** | **−0.1428%** |

**Faster is worse.** Gross −0.069% at 1-minute against −0.059% at 5-minute,
and net −0.143% against −0.133%. The same bill is paid over a smaller move.

And the selectivity curve **inverts**:

| Threshold | Trades | Win % | Mean gross |
|---:|---:|---:|---:|
| 0.103 | 781 | 21.1% | −0.1105% |
| 0.201 | 391 | 24.3% | −0.1351% |
| 0.304 | 79 | 32.9% | **−0.2614%** |

Win rate climbs from 21.1% to 32.9% while mean return falls by a factor of
2.4. This is the tight-stops artefact from the 5-minute curve, in an
undisguised form: the model has learned to pick trades that resolve as small
wins and occasional large losses. A win-rate objective would have called this a
success. It is the reason §9's gate is written on expected value and not on
probability.

The portfolio backtest declines to run at all on this sample — it requires 20
training days and 7 exist — and returns zero trades rather than a result built
on four days of training. That is the intended behaviour.

The OPEN bucket, reachable only at this resolution, is the one positive-gross
cell in the entire study: 20 setups, 55% win rate, +0.053% gross, −0.024% net.
**Twenty observations is not evidence of anything**, and it does not clear cost
even so. It is recorded because it is the only place worth looking again once
more 1-minute history accumulates.

## 10. Walk-forward results

Six folds, cut on day boundaries, one embargo day between train and test, the
model refitted from scratch each fold.

| Mode | Folds | Test n | Base rate | AUC | Brier | Brier (base rate) |
|---|---:|---:|---:|---:|---:|---:|
| Anchored | 6 | 4,293 | 27.0% | 0.544 | 0.1958 | 0.1969 |
| Rolling | 6 | 4,293 | 27.0% | 0.541 | 0.1962 | 0.1969 |

Anchored and rolling agree, so what little signal exists is at least not
obviously non-stationary.

**AUC 0.544 is real but tiny.** Hanley–McNeil standard error at this sample
size is about 0.009, so it is several standard errors above 0.5 — the model is
detecting *something*. The Brier score beats the base-rate model by 0.0011,
which is the honest magnitude of that something.

### Per-head out-of-sample AUC

| Head | Mean | By fold |
|---|---:|---|
| PRICE | 0.545 | 0.52, 0.53, 0.52, 0.56, 0.59, 0.55 |
| FLOW | 0.546 | 0.54, 0.51, 0.54, 0.55, 0.56, 0.58 |
| REGIME | 0.543 | 0.54, 0.54, 0.59, 0.58, 0.52, 0.49 |
| VOLATILITY | 0.540 | 0.50, 0.58, 0.51, 0.56, 0.56, 0.54 |
| MOMENTUM | 0.523 | 0.53, 0.50, 0.53, 0.55, 0.52, 0.53 |
| REVERSAL | **0.489** | 0.50, 0.49, 0.50, 0.48, 0.48, 0.48 |
| BOOK | — | no data |

Two things to read out of this table. First, the FLOW head — built entirely on
proxies, on a feed with no tape — is the joint best. That is mild evidence that
order-flow information is the right place to look, and the reason the depth
recorder matters. Second, **the REVERSAL head is below 0.5 in five of six
folds**: it is consistently, if weakly, anti-predictive. Consistent with the
earlier finding that every bearish candlestick pattern in this repository's
100-year study was inverted, and with `rapid_reversal` being the worst setup by
a wide margin (−0.187% gross).

### Selectivity curve

The single most informative table here. If the model ranks anything, mean
return should rise with the threshold.

| Threshold | Trades | Win % | Mean gross | Mean net |
|---:|---:|---:|---:|---:|
| 0.107 | 4,293 | 27.0% | −0.0567% | −0.1299% |
| 0.195 | 3,864 | 27.4% | −0.0508% | −0.1240% |
| 0.222 | 3,435 | 28.4% | −0.0457% | −0.1188% |
| 0.243 | 3,006 | 29.0% | −0.0453% | −0.1184% |
| 0.258 | 2,577 | 29.5% | −0.0461% | −0.1192% |
| 0.271 | 2,147 | 29.6% | −0.0492% | −0.1225% |
| 0.281 | 1,718 | 29.3% | −0.0544% | −0.1278% |
| 0.293 | 1,289 | 29.4% | −0.0562% | −0.1303% |
| 0.307 | 860 | 30.5% | −0.0500% | −0.1243% |
| 0.328 | 431 | 33.4% | −0.0395% | −0.1140% |

Win rate rises monotonically, 27.0% → 33.4%. Mean return does not: it improves
to −0.0453% at the fourth step, then gets *worse* again through the middle of
the curve. The model finds trades that win more often and win less when they
do — the classic signature of a model that has learned to prefer trades with
tight stops rather than trades with an edge.

**The whole curve is negative, gross, at every level of selectivity.** There
is no threshold at which this becomes a profitable strategy, which is why the
portfolio backtest takes one trade.

## 11. Ablation study

Each row removes exactly one gate and re-runs the entire walk-forward
pipeline. The `skip_*` flags exist only for this study and are never set in any
production path.

| Variant | Trades | Win % | Net P&L | Per trade | Max DD | Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| Full pipeline | 1 | 0.0% | −₹223 | −₹223 | −₹223 | −2.58 |
| No expected-value gate | 1 | 0.0% | −₹223 | −₹223 | −₹223 | −2.58 |
| No adversarial check | 8 | 62.5% | +₹1,574 | +₹197 | −₹387 | 2.98 |
| No tier threshold | 2 | 0.0% | −₹442 | −₹221 | −₹442 | −3.69 |
| **No gates at all** | **1,514** | 25.3% | **−₹88,583** | −₹58.5 | **−₹89,326** | −20.94 |
| No gates, 1 position | 399 | 27.6% | −₹89,673 | −₹225 | −₹89,767 | −14.65 |

### What each row means

**The gates are worth ₹88,360.** Ungated, the five setups lose ₹88,583 of
₹1,00,000 in 40 trading days — an 89% drawdown. The gated system loses ₹223.
That is the entire contribution of the decision layer, and it is real.

**The expected-value gate is redundant.** Removing it changes nothing: 1 trade
either way. Everything it would have rejected is already rejected by the
adversarial check's "the return model expects a loss" question, which fires on
3,256 candidates against the EV gate's 3,245. This is the same class of finding
as the previous ablation, which found two engine gates that had never rejected
a single signal in 58 days. A redundant gate is not harmful, but it is not a
safety layer either, and it should not be counted as one.

**The "no adversarial check" row is noise and must not be read as a result.**
It shows +₹1,574 at Sharpe 2.98 — the best row in the table — on **eight
trades**. Eight. At that sample size the standard error swamps the estimate
completely, and a 62.5% win rate is five wins. Reporting this row as "removing
the adversarial check improves performance" would be exactly the mistake this
whole document exists to avoid. It is here because an ablation that silently
drops its inconvenient rows is not an ablation.

**Concurrency matters, in the wrong direction.** Restricting to one position
loses slightly *more* (−₹89,673 vs −₹88,583) on a quarter of the trades: −₹225
per trade against −₹58.5. Diversification across five names is not adding edge;
it is diluting a negative one, which is what diversification does to a negative
expectancy.

## 12. Paper-trading instructions

Nothing here is promotable, so these are instructions for **research**, not for
a deployment.

```bash
# The full study, reproducible end to end. Reads data, writes a report, and
# has no path to an order.
earner rapid --interval 5m --days 60
earner rapid --interval 1m --days 7        # ultra-short horizons

# Paper trading with the existing engine (unchanged by this work):
export TRADING_MODE=PAPER
earner trade --capital 100000 --preset diversified

# Record Level-2 depth. This is the one thing worth running daily, because
# depth cannot be bought retrospectively.
earner depth --symbols RELIANCE,ICICIBANK,SBIN,HDFCBANK
```

Live trading remains gated behind `TRADING_MODE=LIVE` **and**
`LIVE_TRADING_CONFIRMATION=I_UNDERSTAND_REAL_MONEY`. Both are required, both
are absent, and this work did not touch that gate.

`.earner/KILL` still stops everything, from any shell, without a restart.

## 13. Remaining weaknesses

**Ranked by how much they matter.**

1. **There is no measured edge.** This is not a caveat on the result, it *is*
   the result. Gross return is negative in every decile of model confidence,
   including the top one. The engine is well-built and it is well-built around
   a hypothesis the data does not support.

2. **No order-book history, so the brief's core cannot be tested.** Sections 3
   and 4 ask for order-flow and order-book engines. The order-book engine
   exists and has no data to run on; the order-flow engine runs on estimates
   with ~20% of bars unresolvable. The depth recorder is accumulating. Until it
   has months rather than days, the brief's central hypothesis is untested — not
   disproven.

3. **Sixty days is a short sample.** 5-minute NSE history is capped at ~60
   days by the free feed. That is 40 test days after training, one market
   regime, and no bear market. A negative result on 60 days is more credible
   than a positive one would be, but neither is conclusive.

4. **The 1-minute sample is seven days.** Ultra-short horizons (§2) are the
   part of the brief with the least data behind it.

5. **Retraining is per block, not daily.** Five-day blocks. Daily retraining is
   a straight runtime multiple and would be marginally more realistic.

6. **Latency is modelled as one bar.** On 5-minute bars that is conservative;
   for a genuine seconds-scale strategy it is the wrong unit entirely, and
   nothing here is validated at that scale.

7. **No CPCV, no PBO.** Walk-forward gives one path. Combinatorial purged CV
   would give a distribution and a probability of backtest overfitting. Still
   the right next validation upgrade.

8. **Options and futures segments are costed but not traded.** The cost model
   covers them; no setup detector is specialised for them, and expiry effects
   (§1) are not modelled at all.

9. **`_implied_volume` is a constant.** The labelled outcome does not carry the
   entry bar's volume, so the participation cap uses ₹10 lakh per bar. It is
   conservative for an NSE large-cap, and it is a stand-in.

10. **Impact coefficient 0.6 is uncalibrated.** It is the middle of the
    published range, chosen so it could not be tuned to the answer. Real
    calibration needs fill data this system does not have.

# What candlestick patterns actually do

Measured, not quoted. 23 global instruments — US, Europe, Asia, India,
commodities, crypto — **191,118 daily bars**, deepest series 56.6 years
(S&P 500, 1970). 19 patterns, 10-day forward returns, conditioned on market
regime.

No textbook definitions were trusted. Every number below is what the pattern
did, not what it is supposed to do.

---

## The number that changes everything

A **randomly chosen day** returns **+0.53%** over the next 10 days.

That is the base rate, and almost every published candlestick statistic
ignores it. "This pattern is right 57% of the time" is meaningless when the
market rises 57% of the time anyway.

Every result below is therefore reported three ways: the raw return, the base
rate, and the difference — which is the only one that is an edge.

---

## Every pattern, ranked honestly

10-day forward return, minus the base rate, minus a 0.22% round trip.

| Pattern | Raw | Base | **Edge** | After cost | Verdict |
|---|---:|---:|---:|---:|---|
| golden_cross | +0.69 | +0.53 | **+0.16** | −0.06 | drift only |
| breakout_20d | +0.67 | +0.53 | **+0.14** | −0.08 | drift only |
| rsi_oversold | +0.66 | +0.53 | **+0.13** | −0.09 | drift only |
| bullish_engulfing | +0.60 | +0.53 | **+0.07** | −0.15 | drift only |
| inside_bar | +0.60 | +0.53 | **+0.07** | −0.15 | drift only |
| momentum_12m | +0.59 | +0.53 | **+0.06** | −0.16 | drift only |
| three_white_soldiers | +0.58 | +0.53 | **+0.05** | −0.17 | drift only |
| doji | +0.58 | +0.53 | **+0.05** | −0.17 | drift only |
| gap_up | +0.51 | +0.53 | **−0.02** | −0.24 | drift only |
| hammer | +0.41 | +0.53 | **−0.12** | −0.34 | WRONG WAY |
| reversal_1m | −0.46 | +0.53 | **−0.98** | −1.20 | WRONG WAY |
| gap_down | −0.47 | +0.53 | **−1.00** | −1.22 | WRONG WAY |
| outside_bar | −0.51 | +0.53 | **−1.04** | −1.26 | WRONG WAY |
| bearish_engulfing | −0.54 | +0.53 | **−1.07** | −1.29 | WRONG WAY |
| death_cross | −0.59 | +0.53 | **−1.12** | −1.34 | WRONG WAY |
| breakdown_20d | −0.62 | +0.53 | **−1.14** | −1.36 | WRONG WAY |
| three_black_crows | −0.67 | +0.53 | **−1.20** | −1.42 | WRONG WAY |
| shooting_star | −0.69 | +0.53 | **−1.22** | −1.44 | WRONG WAY |
| rsi_overbought | −0.88 | +0.53 | **−1.41** | −1.63 | WRONG WAY |

**Patterns with a real edge after costs: 0 of 19.**

---

## The three lessons

### 1. Bullish patterns are measuring drift, not predicting

Every bullish pattern returns between +0.51% and +0.69%. The base rate is
+0.53%. The best of them adds **0.16 percentage points** — and the round trip
costs 0.22.

They are not wrong. They are simply detecting that you are in a market that
rises, which you could have known by owning it and doing nothing.

### 2. Bearish patterns are inverted

This is the finding that should change how you trade. Traded as short signals,
**every single bearish pattern loses money** — and not marginally. Shooting
star loses 1.44%. Three black crows loses 1.42%. RSI overbought loses 1.63%.

Price tends to **rise** after these patterns, because equities drift upward and
any rule that puts you short is fighting that drift. A "reliable" bearish
pattern that is right 45% of the time in a market that rises 57% of the time
is a losing rule with a good reputation.

### 3. Everything looks better in high volatility — including nothing

| Regime | Base rate | Typical pattern return |
|---|---:|---:|
| High volatility | **+0.93%** | +0.8 to +1.6% |
| Low volatility | **+0.34%** | +0.2 to +0.5% |

Patterns appear two to three times stronger in volatile markets. So does the
market. The ratio barely moves. Backtests that only report raw returns in
volatile periods are showing you the weather, not the strategy.

---

## Where the base rate hides

| Regime | 10-day base return | Observations |
|---|---:|---:|
| Bull (60d trend > +2%) | +0.62% | 100,355 |
| Bear (60d trend < −2%) | +0.49% | 57,432 |
| Flat | +0.32% | 31,698 |

Note the second row. Even in a **downtrend**, the average 10-day forward
return is **positive**. That is why shorting on candle signals fails: the
sample you are betting against still drifts up.

---

## What this does not say

It does not say markets are unpredictable. It says these nineteen patterns,
as commonly defined, do not carry enough information to pay for a 22 basis
point round trip — and a retail intraday account pays far more than that.

An edge that survives measurement exists in this repository: cross-sectional
momentum held for a quarter, ~+15% annualised excess. It is not a candle
pattern, it is not intraday, and it is still unproven out of sample.

## Reproduce it

```bash
python -c "from quant_os.validation.run_pattern_study import run; run()"
```

Full per-regime results: `reports/candle_knowledge.json`.

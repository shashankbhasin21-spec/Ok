# Codebase audit

Audited at commit `131498b`, branch `claude/ai-agents-real-money-bpqgqt`.
Kotak findings verified against the v2.0.2 SDK source and its response
documentation, not from memory. No profitability is claimed anywhere in this
document; the strategies tested lose money on real data.

---

## 1. Current architecture

Twelve modules, 3,455 lines, 201 passing tests.

```
session.py     deployment gate (TRADING_MODE + confirmation), market clock, is_stale
risk.py        Book (fills, FIFO P&L, costs) + RiskManager (the deterministic gate)
strategy.py    indicators, three strategies, regime classifier
engine.py      the loop: regime -> signal -> EV -> risk veto -> order -> managed position
orders.py      write-ahead order store, idempotency, Kotak order-book translation, Reconciler
broker.py      Broker protocol; KotakBroker (live) and PaperBroker share one interface
live.py        preflight, TOTP/MPIN connect, quote polling -> CandleBuilder, run loop
marketdata.py  real NSE history via Yahoo chart endpoint, cached, gaps dropped
backtest.py    walk-forward replay through the same Engine used live
research.py    hypothesis search with three-way split, embargo, multiple-testing threshold
simulate.py    synthetic day for smoke-testing the whole path
dashboard.py   HTML report generated from the engine's own SQLite
```

**What is genuinely good and should be preserved:**

- Position management runs *before* and *independently of* signal generation, so a
  crashing strategy cannot strand a live position.
- Orders are journaled to disk before submission.
- The risk gate is pure arithmetic with no model in the path. **No LLM touches the
  order path anywhere in the codebase** — this holds structurally, not by policy.
- Paper and live share the strategy, risk, book and engine code; only the broker
  adapter differs.
- Reconciliation exists and can halt trading on divergence.

---

## 2. Correctness defects

### 2.1 CRITICAL — bar count silently used as wall-clock duration

`opening_range(candles, minutes=15)` slices `candles[:15]`. That is **15 bars, not
15 minutes.**

| Bar interval | `range_minutes=15` actually means |
|---|---|
| 1-minute | 15 minutes ✓ |
| 5-minute | **75 minutes** ✗ |

Consequences:

- Every backtest reported for "15-minute ORB" on 5-minute bars was really a
  **75-minute** opening range.
- The signal's own `thesis` string says `"the 15m high"` — the log actively
  misreports what the strategy did.
- `volume_multiple` baseline uses the same slice, so the volume filter's reference
  window is wrong by the same factor.

### 2.2 CRITICAL — live and backtest warm-up are different durations

| Constant | File | Bars | On its usual interval |
|---|---|---|---|
| `WARMUP_CANDLES = 40` | `live.py` | 40 | 40 minutes (1m bars) |
| `WARMUP_BARS = 40` | `backtest.py` | 40 | **200 minutes** (5m bars) |

The strategy that was backtested is therefore not the strategy that would run live.
This breaks the live/paper parity the architecture otherwise maintains.

`VWAPMomentum` and `MeanReversion` both gate on `len(candles) < 40`, inheriting the
same ambiguity.

Indicator periods (`ema(9)`, `ema(21)`, `atr(14)`, `rsi(14)`) are legitimately bar
counts by convention, but their *duration* still changes with interval and nothing
records which interval a result came from.

### 2.3 CRITICAL — reconciliation reads fields that do not exist

`Reconciler.check_positions()` reads `flBuyQty`, `flSellQty`, `buyQty`, `sellQty`,
`quantity`. The documented Kotak v2 `positions()` row contains:

```
sym, trdSym, trnsTp, fldQty, qty, avgPrc, prod, exSeg, nOrdNo, ...
```

**None of the five field names the code looks for.** Every real position parses as
quantity `0`. The check built to catch "the engine is long something it believes it
sold" would either flag every position as a mismatch, or pass silently while the
broker holds live stock.

### 2.4 CRITICAL — `is_stale()` is defined and never called

Dead safety code. `grep -rn is_stale earner/` returns only the definition. The engine
will trade on a quote of any age.

### 2.5 HIGH — no intrabar execution model

`ManagedPosition.exit_reason(price)` is called with the bar's **close** only. If a bar
traded through both the stop and the target, the engine sees whichever the close
happens to be nearer — effectively a coin flip resolved by luck rather than by a
declared assumption. There is no `WORST_CASE` / `OHLC_PATH` / `TICK_REPLAY` mode.

### 2.6 HIGH — same-bar fill bias, measured

The engine signals from `candles[-1]` and the paper broker fills at
`candles[-1].close` — the exact price that produced the signal. Measured over 30 days
and 5 symbols:

```
Same-bar close fill (current harness): -19,923   380 trades
Next-bar open fill  (achievable live): -20,783   404 trades
Optimism baked in:                        +860
```

Small only because the strategies lose either way; the direction is what matters.

### 2.7 MEDIUM — risk is calculated once, not cross-checked

The strategy proposes `entry/stop/target`; `Engine.consider()` sizes from
`signal.risk_per_share`; `RiskManager.check()` sizes independently from
`stop_loss_pct`. The two are then combined with `min()`. There is no assertion that
the strategy's own risk view and the risk engine's agree, so a malformed signal is
silently accepted as long as the smaller of the two numbers is sane.

### 2.8 MEDIUM — duplicate-order protection is bypassable

`Engine.send()` falls back to `broker.place()` directly when `orders=None`, which is
the constructor default. Idempotency is therefore opt-in.

### 2.9 MEDIUM — no external kill switch

`flatten_all()` is sound but reachable only via `Ctrl-C` or an internal risk halt. An
unattended run cannot be stopped from outside the process.

---

## 3. P&L accounting

Fixed during this session, recorded here because the class of error matters:
`Book.realized_pnl()` originally walked fill prices only, so brokerage was charged to
the paper broker's cash and then ignored by every reported number — **including the
daily loss limit**, which therefore stopped later than it promised. `Fill` now carries
`cost`, the book persists it, and `realized_pnl()` subtracts it; `gross_pnl()` keeps
the before-costs figure available and is documented as the number that flatters paper
engines.

The giveaway was a multi-day run reporting +17,280 gross on 5,000 of capital against
random-walk prices, which cannot happen.

---

## 4. Data

- Single free source (Yahoo chart endpoint, `.NS`). No key, no SLA, no cross-check.
- Nulls are dropped rather than forward-filled; bars outside 09:15–15:30 IST are
  discarded. Both verified by test.
- **No corporate-action adjustment.** A 1:2 split appears as a −50% bar, which would
  trigger every stop simultaneously and be booked as a real loss.
- **Survivorship bias.** The universe is today's large caps.
- **Volume is unreliable** — several fetched bars carry `volume = 0`, which silently
  disables the ORB volume filter on those bars.
- **Depth is the ceiling.** ~60 days of 5-minute bars, ~7 days of 1-minute. Validating
  an intraday edge needs roughly 900 days. Procurement problem, not engineering.
- **No tick journal** — today's data is discarded rather than banked.

---

## 5. Kotak Neo API v2

Nine methods used; nine that matter unused.

| Method | Used | Assessment |
|---|---|---|
| `totp_login` / `totp_validate` | yes | correct two-step flow |
| `place_order` | yes | enum keys verified against `settings.py`; SDK swallows exceptions and returns `{'Error': …}`, now inspected |
| `order_report` | yes | used for confirm + reconcile |
| `positions` | **wrong** | reads fields the response does not contain (§2.3) |
| `quotes` | polled | 5s per symbol; documented HTTP 429 risk, unnecessary latency |
| `scrip_master`, `limits`, `cancel_order` | yes | reasonable |
| `trade_report` | **no** | the authoritative fill record, never consulted |
| `margin_required` | no | pre-trade margin check available; 4× guessed locally instead |
| `modify_order` | no | no amend capability |
| `order_history` | no | per-order transitions unavailable for audit |
| `subscribe` / `un_subscribe` | no | WebSocket market data unused — this is why quotes are polled |
| `subscribe_to_orderfeed` | no | real-time order updates unused; `confirm()` polls at 0.5s |
| `logout` | no | session never torn down |

**Unsafe assumptions:** position fields invented; fills inferred from orders rather
than trades; margin guessed rather than queried; polling where streaming exists; no
heartbeat/reconnect/resubscribe because there is no socket.

---

## 6. Security

| Check | Result |
|---|---|
| Secrets in git | clean — only `.env.example` with placeholders |
| `.gitignore` | covers `.env`, `.earner/`, `__pycache__`, `*.egg-info` |
| TOTP / MPIN | `getpass` at runtime, never stored, never logged |
| Credential logging | **risk** — `_sdk_error()` returns `str(response)`; an error payload containing a token would land in an exception message and the event log |
| Session teardown | **risk** — `logout()` never called, tokens stay valid after exit |
| Order DB at rest | unencrypted (gitignored) |
| CI secret scanning | **none — no CI/CD exists at all** |
| Dependency surface | minimal; trading stack verified to import with zero third-party packages |

---

## 7. Race conditions and state management

- `sqlite3.connect()` is called without `check_same_thread=False` and without a
  documented threading model. Today everything is single-threaded, so this is latent
  rather than active — but a WebSocket feed would introduce a second thread and break
  it silently.
- `Engine.positions` is in-memory only. Orders survive a restart; **the exit plans
  attached to positions do not.** After a crash the engine reconstructs what it owns
  from the broker but not the stop and target it intended.
- No sequence numbers on events, so deterministic replay is not currently possible.

---

## 8. Missing production safeguards

Research: factor grammar, factor store, experiment registry, lineage, CPCV, purged CV,
PBO, PSR, DSR, Monte Carlo, capacity, parameter-stability surfaces, adversarial review.

Execution: WebSocket data, order feed, limit/peg orders, slicing, latency modelling.

Operations: CI, external kill switch, alerting, structured log redaction, crash
recovery of position intent, Docker/runbook/DR documentation.

Lifecycle: strategy states, promotion criteria, automatic demotion, versioning,
rollback.

Tests: no dedicated tests for `dashboard`, `simulate`, or `marketdata` fetching; no
reconciliation tests against **real** Kotak response fixtures — which is precisely
what would have caught §2.3.

---

## 9. Strategy performance on real data

58 trading days, 10 NSE large caps, walk-forward through the production engine.

| Strategy | Gross P&L | Trades | Win rate |
|---|---|---|---|
| `opening_range_breakout` | −6,623 | 264 | 14% |
| `vwap_momentum` | −8,858 | 538 | 9% |
| `vwap_mean_reversion` | −12,484 | 894 | 3% |
| buy-and-hold baseline | −0.6% | — | — |

All three lose **gross, before brokerage**, against a flat market. A 19-hypothesis
parameter search produced no survivor, and correctly reported it lacked the
statistical power to have found one: 10 validation days give a Sharpe estimate
accurate to ±5.00, and separating a Sharpe-1.0 strategy from luck among 19 candidates
needs roughly 882 days.

Note that these results were produced with the §2.1 bug active, so the strategies
tested were not the strategies described. They must be re-measured after the fix.

---

## 10. Priority order

0. CI running the test suite and a secret scan on every push.
1. Bar-interval / duration correctness (§2.1, §2.2) — invalidates prior results.
2. Reconciliation field mapping + `trade_report` (§2.3).
3. Wire `is_stale()`; external kill switch; make the order store mandatory (§2.4, §2.9, §2.8).
4. Intrabar execution modes with `WORST_CASE` default; next-bar fills (§2.5, §2.6).
5. Canonical risk cross-check (§2.7).
6. Event store with sequence numbers, then validation, then the red team.
7. Factor/research generation **last**, behind every filter.

Steps 6 and 7 are ordered deliberately: building a hypothesis generator before its
filter turns a research loop into a machine for manufacturing false positives.

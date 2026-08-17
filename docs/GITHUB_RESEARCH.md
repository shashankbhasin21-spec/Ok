# Open-source research references

Architecture and publicly documented techniques studied while designing this system.
No proprietary code was copied. Each entry records what was adopted, what was
rejected, and why.

Nothing here is evidence that any of these projects, or their users, are profitable.

---

## Validation and overfitting

### [eslazarev/purged-cross-validation](https://github.com/eslazarev/purged-cross-validation)

*scikit-learn-compatible time-series CV: purging, embargo, combinatorial purged CV,
and deflated Sharpe ratios.*

**Useful technique.** Purging removes training samples whose label windows overlap the
test window; the embargo drops a further gap after each test fold. CPCV builds many
backtest *paths* from combinations of held-out groups rather than a single split, so
the variance of the result is observable instead of assumed.

**Adopted:** the embargo concept, already implemented in `research.split_days()` — a
gap between train and validation because indicators look backwards and adjacent bars
leak across the boundary. The deflated-Sharpe idea is implemented in
`research.expected_max_sharpe()`.

**Not yet adopted:** CPCV proper. The current split is a single chronological
three-way cut, which gives one path and therefore no distribution. This is the right
next validation upgrade.

**Rejected:** taking the library as a dependency. The trading stack currently imports
zero third-party packages, which is worth keeping; the two techniques we need are ~40
lines each.

### [esvhd/pypbo](https://github.com/esvhd/pypbo)

*Probability of Backtest Overfitting.*

**Useful technique.** PBO estimates how often the in-sample best performer is
below-median out of sample, across combinatorially split data. It answers "is my
selection process itself broken", which is a different question from "is this strategy
good".

**Adopted:** the framing. Our `Verdict` reports a luck threshold and refuses to
conclude when the sample cannot support a conclusion — the same instinct.

**Not yet adopted:** PBO as a computed number. It requires the CPCV path machinery
above.

---

## Execution and backtest engines

### [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader)

*Production-grade Rust-native engine with a deterministic event-driven architecture.*

**Useful architecture.** The central idea is *research-to-live semantic parity*: the
same strategy code, the same event flow, the same order state machine in backtest and
in production, with only the venue adapter swapped. Nautilus also models latency and
fills explicitly rather than assuming a fill at the signal price.

**Adopted:** parity is already the design here — `PaperBroker` and `KotakBroker`
implement one `Broker` protocol and the strategy, risk and book code cannot tell them
apart. Reinforced by this reference rather than derived from it.

**Adopting now:** explicit fill modelling. The audit measured a same-bar fill bias in
our harness; the fix is a declared execution assumption (`WORST_CASE` default) instead
of an accidental one.

**Rejected:** adopting Nautilus itself. It is a better engine than this one, but
migrating would discard a working Kotak adapter, risk gate and reconciliation layer to
gain features we cannot yet use — the binding constraint here is data volume and
absence of edge, not engine throughput.

### VectorBT / Backtrader / LEAN / Zipline

**Useful contrast.** VectorBT treats backtesting as array mathematics and evaluates
thousands of parameter combinations at once. That is exactly the capability that
makes multiple-testing bias dangerous: it is trivially easy to test 10,000 variants
and keep the best.

**Adopted:** nothing structural.

**Rejected, deliberately:** mass parameter sweeps as a research method, unless paired
with a correction for the number of trials. Our search reports the best-of-N Sharpe
that luck alone produces and requires candidates to clear it.

---

## Autonomous research loops

### [rock-mind/autoquant](https://github.com/rock-mind/autoquant)

*Autonomous A-share strategy optimisation driven by an AI agent.*

**Useful architecture.** Hypothesis → backtest → composite score → keep or revert,
with every accepted change captured as a git commit so the experiment history is
reconstructible. Scoring weights: Sharpe 30%, max drawdown 25%, annual return 20%,
win rate 15%, profit factor 10%.

**Adopted:** the loop shape and the scoring weights, in `research.py`. The balance is
sensible — risk-adjusted return dominates, drawdown is nearly as important as return,
and raw win rate is deliberately minor.

**Rejected:** its acceptance rule. The documentation states *"only the validation
score determines whether a change is kept or dropped"* and documents **no
multiple-testing correction**. Run that loop five hundred times and the validation set
has been read and steered by five hundred times — it is a training set now, and its
score is not evidence. This matters more with an AI agent, which can run five hundred
iterations before lunch.

**Our departure:** a three-way split where the test set is read exactly once, for one
survivor, at the very end; plus the luck threshold above.

### [whereareyouman/AStockArena](https://github.com/whereareyouman/AStockArena)

*Comparative evaluation of LLM trading agents on the STAR Market.*

**Useful architecture.** Shared market snapshots so competing agents see identical
data, and process isolation per agent. Both are good hygiene for fair comparison and
for stopping one agent's crash taking down the harness.

**Adopted:** the shared-snapshot principle — `backtest.replay()` hands every
hypothesis the identical `data` dict rather than re-fetching per trial.

**Rejected:** LLMs making trading decisions. In this system no model touches the order
path; models may propose hypotheses and review results only.

### [TauricResearch/TradingAgents](https://github.com/tauricresearch/tradingagents)

*Multi-agent LLM framework: Analyst / Researcher / Trader / Risk roles with bull-bear
debate.*

**Useful architecture.** Role separation, and specifically a reviewer that does not
share the researcher's reasoning.

**Adopted as a plan:** the adversarial reviewer must see only the formal hypothesis,
code, data specification and results — never the researcher's chain of thought — and
its veto must be binding. Not yet implemented.

**Rejected:** debate as a decision mechanism for order placement. Two models agreeing
is not evidence about the market; it is evidence about the models.

---

## Indian market specifics

### [buzzsubash/algo_trading_strategies_india](https://github.com/buzzsubash/algo_trading_strategies_india)

*NSE/BSE option selling in NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX.*

**Useful reference.** Broker-adapter separation for an Indian retail context, and the
practical shape of index-option workflows.

**Adopted:** nothing directly yet — our system is equity-intraday only.

**Noted for Phase 3:** option selling carries assignment and gap risk that this
system's risk engine does not currently model at all. Adding options without adding
that modelling would be worse than not adding them.

### OI / option-chain analysis projects

[HawkEyeCoding/nse-oi-analysis](https://github.com/HawkEyeCoding/nse-oi-analysis),
[raval137/NIFTY-BANKNIFTY-CALL-PUT-Live-Market-Analysis](https://github.com/raval137/NIFTY-BANKNIFTY-CALL-PUT-Live-Market-Analysis),
[ashok-kollipara/options-oi](https://github.com/ashok-kollipara/options-oi)

**Useful technique.** Polling the NSE option chain for OI, change in OI, and PCR at a
fixed cadence, and deriving strike concentration from it.

**Adopted:** nothing yet.

**Rejected as signals:** treating OI or PCR as directional predictors. These projects
generally present OI shifts as sentiment indicators without out-of-sample evidence
that the resulting signal is profitable after costs. They are reasonable *features*;
they are not validated *alpha*, and our pipeline must treat them as candidates
requiring the same validation as anything else.

---

## What this survey changed

1. **Fill modelling becomes explicit** rather than incidental (Nautilus).
2. **CPCV and PBO are the next validation upgrades**, ahead of any new strategy
   (purged-cross-validation, pypbo).
3. **The red-team reviewer must not see researcher reasoning** (TradingAgents).
4. **AutoQuant's acceptance rule is the thing to avoid**, not to copy — it is the
   clearest available example of a well-engineered loop with a statistically fatal
   decision criterion.
5. **Option-chain metrics enter as features, never as signals.**

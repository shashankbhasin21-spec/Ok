# The earning plan, corrected

Three months in, this project has earned **₹0**. The ledger has zero payments
in it, and that is the number that matters. This document records what the
evidence says to do about it, including where it contradicts advice I gave
earlier in the same repository.

## The correction

`docs/where-to-sell.md` found a real 21× price difference between Indian
marketplace rates ($242/deal) and US direct rates ($1,800–$5,000), and
concluded: treat marketplaces as practice, sell direct.

**That advice optimises the wrong variable for this situation.** It maximises
revenue *per deal*. The actual problem is that no deal has ever closed, and a
first sale is worth far more than its price:

- it produces the review that makes every later proposal credible
- it proves the delivery pipeline end to end on a real client
- it converts "I can do this" into "I have done this"

So the ordering changes. Not the destination — the destination is still US
direct work at US prices. The first step is different.

| | Cold outreach (freight audit) | Marketplace bidding |
|---|---|---|
| Demand | must be created | **already exists** |
| Trust needed to start | high — unknown offshore vendor | low — platform holds escrow |
| Cycle | 2–3 weeks | days |
| Deliverability risk | account suspension | none |
| Price | $1,500–$2,500 | $100–$500 |
| Review produced | no | **yes** |

Run the marketplace first for the review, then the outreach at real prices.
Both machines are built; they are not alternatives, they are a sequence.

## What the evidence actually says

From published 2026 figures on freelancer cold-start:

- A client receives their **first proposal within about three hours** of posting.
- The single highest-return habit is reaching a job **inside the first hour**,
  before the crowd arrives and a zero-review profile is buried under proven ones.
- **Ten tailored proposals to jobs under an hour old beat fifty generic ones**
  to stale listings.
- **$100–$500 fixed scopes** carry low enough risk that a client will take a
  chance on a profile with no reviews.
- Expect **20–30 proposals before the first job**. That is the normal
  conversion rate, not failure.
- First contract typically lands in **2–6 weeks** of focused effort; passive
  profile-and-wait takes 3–6 months.

## Why speed is the only edge here — and why it is real

Every other edge this project chased was contested by better-resourced people.
Intraday NSE: measured −5.9bp per trade before costs, against firms with
colocation. Solana arbitrage: professional searchers running validators.

Being first to a job posting is not contested that way. There is no latency
war, no order book, no firm with a faster wire. **There is only whether you
were awake.** A program is awake at 03:00 and a person is not.

That is the whole thesis of `earner/watch.py`, and it is the first edge in this
repository that survives being stated plainly.

## The machinery

```bash
earner watch --every 300          # poll boards, rank fresh postings, alert
earner watch --once               # one sweep
earner pipeline run --live        # cold outreach, once there is a review
```

`watch.score()` multiplies four terms, so a posting has to clear all of them:

```
freshness × fit × competition × trust
```

Freshness decays with a 60-minute half-life and hits zero at six hours. A
perfect match posted nine hours ago loses to a decent one posted nine minutes
ago, and there is a test asserting exactly that — if that ordering ever
inverts, the module has stopped doing its job.

The profile floor is **$100**, deliberately lower than the bidding agent's
$150. The first three jobs are for the reviews, not the money. Raise it once
the profile has a history.

## What is still yours

The watcher finds and ranks. It does not submit — Upwork's API does not permit
programmatic proposals, and writing the bid is where being a person is worth
something anyway.

So the loop is:

1. `earner watch` runs and alerts you (phone, terminal, wherever)
2. **You write and send the proposal, ideally within the hour**
3. `earner pipeline` handles everything after the reply: quote, invoice, collect

Realistically that is 20–30 proposals over 2–6 weeks for the first contract.
The machine removes the part that requires being awake. It cannot remove the
part that requires being a person, and no version of this ever will.

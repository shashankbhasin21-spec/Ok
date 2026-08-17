# Connecting the engine to your Kotak Neo account

Every step below is something you do on your own machine. None of your
credentials pass through anyone else, and none of them are written to disk by
this software.

Capital assumed throughout: **₹1,00,000**.

---

## Step 1 — get the code running (5 minutes, no account needed)

```bash
git clone https://github.com/shashankbhasin21-spec/Ok.git
cd Ok
git checkout claude/ai-agents-real-money-bpqgqt

python -m earner.cli trade --mode simulate --capital 100000 --preset diversified
```

That runs a full trading day and prints every decision. It needs **no
credentials, no internet and no installed packages** — the engine, the risk
limits and the simulator are standard-library Python 3.11+.

If it prints a day of signals, fills, stops and a final P&L, the software works
on your machine. That is the whole product, running.

---

## Step 2 — get your Kotak API credentials

In the **Kotak Neo app**: `Invest → Trade API`.

You are looking for three values:

| Value | Where it comes from |
|---|---|
| Consumer Key | Shown in the Trade API section after you enable it |
| Mobile number | The one registered with Kotak, with country code (`+91…`) |
| UCC | Your Kotak client code, on your contract notes and in the app |

You also need **TOTP enabled** on the account. Kotak's API login is a two-step
TOTP flow — there is no way around it, and that is a good thing.

> **Two values you will NOT put anywhere:** your **MPIN** and your **TOTP**.
> The TOTP changes every 30 seconds so it cannot be stored, and storing an MPIN
> would put your account one leaked file away from anybody. Both are typed at
> the terminal each time the engine starts.

---

## Step 3 — install the Kotak SDK

```bash
pip install -e ".[kotak]"
```

This is the only dependency the live path adds.

---

## Step 4 — set the three values

```bash
export KOTAK_CONSUMER_KEY="your-consumer-key"
export KOTAK_MOBILE="+919999999999"
export KOTAK_UCC="YOURUCC"
```

Put these in a file the shell reads at startup (`~/.bashrc`, or a `.env` you
source) so you are not retyping them. **Do not commit that file.**

---

## Step 5 — run the preflight

```bash
python -m earner.cli trade --mode check
```

This checks everything and names every problem at once, rather than failing on
the first one at 09:20 with the market open:

```
Not ready for live:
  ✗ the gate is shut: TRADING_MODE=DEVELOPMENT, confirmation missing
  ✗ KOTAK_CONSUMER_KEY is not set
  ✗ market is not open: pre-open (opens 09:15)
```

Fix what it lists and run it again until it says ready.

---

## Step 6 — open the live gate

Two environment variables, both exact. This is deliberately awkward: nobody
types the second one by accident.

```bash
export TRADING_MODE=LIVE
export LIVE_TRADING_CONFIRMATION=I_UNDERSTAND_REAL_MONEY
```

Miss either one and no real order can be placed, whatever else the program
believes. The check runs again immediately before *every single order*, not
just at startup.

---

## Step 7 — go live

```bash
python -m earner.cli trade --mode live --capital 100000 --preset diversified --max-minutes 60
```

What happens, in order:

1. **Preflight** — refuses before touching the network if anything is wrong.
2. **Login** — prompts for your TOTP, then your MPIN. Neither is echoed or stored.
3. **Instrument master** — downloads the real symbol-to-token map. The engine
   refuses to trade a token it had to guess.
4. **Reconciliation** — asks Kotak what it thinks you already hold and compares
   it against its own records. **Any disagreement halts the desk before a single
   order goes out.**
5. **Warm-up** — about 40 minutes. The Kotak API serves live quotes but not
   historical candles, so the indicators have to be built from live ticks. The
   first signal of the day cannot come before roughly 09:55. Anything claiming
   otherwise would be trading on indicators computed from three data points.
6. **Trading** — up to 8 simultaneous positions, each with its stop and target
   attached at entry.
7. **Square-off** — everything is flat by 15:15, before the MIS cutoff.

Start with `--max-minutes 60`. Press `Ctrl-C` at any point and it flattens
every position before exiting.

---

## What stops it

| Stop | Fires when |
|---|---|
| Daily loss limit | Down ₹5,000 including open positions — flattens and halts |
| Losing streak | 4 losses in a row — the regime has probably turned |
| Margin ceiling | Gross holdings reach 4× capital |
| Sector cap | One sector reaches 7% of risk |
| Trade cap | 20 trades — churn is what makes small accounts lose |
| Square-off | 15:15, every day, unconditionally |
| Reconciliation | Kotak and the engine disagree about what you hold |
| `Ctrl-C` | Immediately, flattening everything on the way out |

---

## Before you risk real money

Run **step 1 daily for a week** and watch what it does. Then run live with
`--max-minutes 30` and a mental note that the first live day is for verifying
plumbing, not for making money.

There is no backtest on real NSE history yet, which means **nobody — including
me — currently knows whether these strategies are profitable.** The safeguards
are proven by 167 tests. The edge is not proven at all. Those are two very
different claims and it matters that you hold them separately.

## The cost you cannot avoid

Brokerage is charged per order and does not shrink with your account. Eight
positions is sixteen orders a day. On ₹1,00,000 that is roughly **₹1,200 a day,
about 1.2% of your capital, before any profit at all**. You need to clear that
every single day just to finish level.

This is why the trade cap is 20 and not 200. On a ₹1,00,000 account, trading
more is not more chances to win — it is a larger guaranteed bill.

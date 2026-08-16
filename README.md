# earner

A small firm of AI agents that does real client work and bills real money through real payment rails. You talk to the CEO; the CEO runs the staff.

```
                    ┌───────────┐
        you ◄──────►│    CEO    │  reports settled cash, issues directives
                    └─────┬─────┘
        ┌─────────┬───────┼────────┬────────────┬───────────┐
   acquisition  delivery  product  growth   collections  quality
     pitches    does the  builds   Reels,    chases      reviews
     real leads paid work products ads, DMs  invoices    before ship
        └─────────┴───────┼────────┴────────────┴───────────┘
                    ┌─────┴─────┐
                    │  ledger   │  revenue = settled payments only
                    └───────────┘
```

## What it actually does

- **Reads real demand.** Client emails via Gmail (IMAP), lead files, Instagram comments. Nothing is invented.
- **Prices and invoices.** Stripe invoices with a hosted payment page, sent to a real customer.
- **Waits for the money.** Work starts only after the payment provider confirms settlement.
- **Does the work.** Claude produces the deliverable, grounded in live web research rather than model memory.
- **Checks it.** A separate quality agent reviews against the invoiced scope before anything ships.
- **Markets it.** Researches current demand, writes Reels, renders real MP4s, replies to real comments, creates ad campaigns (paused by default).
- **Collects.** Escalating dunning on overdue invoices until the provider says paid.
- **Keeps honest books.** Settled cash, outstanding invoices, and token spend are three different numbers and always stay separate.

## Install

```bash
pip install -e ".[stripe,dev]"
sudo apt install ffmpeg      # optional; needed for Instagram Reels (MP4)
```

## Five minutes to a first run

```bash
export ANTHROPIC_API_KEY=sk-ant-...

earner target 5000 --hours 50                 # set the goal
earner inbox requests job-1 \
  --email client@example.com \
  --title "Competitor brief" \
  --brief "Compare the top 3 CRM vendors for a 40-person agency."
earner run --approve cli                       # you approve each outbound step
earner status                                  # the books
earner ceo                                     # talk to the CEO
```

Sandbox is the default: invoices are simulated, nothing is sent, no card is charged. Revenue stays at $0 because no real money moved — that is the point.

## Going live

```bash
export EARNER_MODE=live
export STRIPE_API_KEY=sk_live_...
earner run --live --approve cli
```

Live mode refuses to start without a Stripe key. `--approve auto` (unattended) additionally requires `EARNER_ALLOW_LIVE_AUTOAPPROVE=1`, so nobody auto-sends real invoices by leaving a flag on from a dry run.

## Connecting the channels

```bash
earner connect
```

Prompts for each credential, **proves it works before saving** (a real IMAP login and SMTP handshake for Gmail; a real Graph API call that reads your account back for Instagram), then writes `.env` with owner-only permissions. A credential that fails at 3am inside an autopilot loop is worse than one that never saved, so it fails at setup instead.

### Gmail ingestion is label-scoped on purpose

The firm only reads mail under the **`earner/requests`** label — not your whole inbox. A real inbox is mostly bank alerts, OTPs, receipts and newsletters; an agent pointed at all of it will happily scope and price an OTP notification. Create the label, add a Gmail filter that routes client mail into it, and the firm sees only real work. Anything automated that slips through is filtered again by sender and subject.

| Channel | Environment | Notes |
|---|---|---|
| Claude | `ANTHROPIC_API_KEY` | Or `ant auth login`. Without it the agents cannot think. |
| Stripe | `STRIPE_API_KEY` + `EARNER_MODE=live` | Real invoices, real settlement. |
| Gmail | `GMAIL_USER`, `GMAIL_APP_PASSWORD` | Enable 2-Step Verification, then create an [App Password](https://myaccount.google.com/apppasswords). Mail under the `earner/requests` label becomes briefs; approved replies are sent. |
| Instagram | `INSTAGRAM_USER_ID`, `INSTAGRAM_ACCESS_TOKEN` | Needs a **Business or Creator** account linked to a Facebook Page, a Meta app with `instagram_content_publish`, and a long-lived token. |

Instagram fetches media from a public URL — it cannot read a local file. Render first, host the MP4 anywhere public, then:

```bash
earner publish --ref post-abc123 --video-url https://your-host/reel.mp4
```

## Getting leads in

```bash
# a prospect you know of
earner lead add --company "Northwind Logistics" --email ops@northwind.co.uk \
  --notes "Freight quote desk is manual, 3-day turnaround" \
  --evidence "Two open 'quote desk coordinator' roles on their careers page"

# a real public job posting — Claude reads the page into a lead
earner lead from-url --url https://www.upwork.com/jobs/~012345

earner lead list

# real postings captured from a live board (see docs/channel-math.md first)
earner lead import --file seeds/leads-freelancer-2026-08-16.json
```

Intake refuses what cannot be sold to: placeholder domains (`example.com`, `yourcompany.com`), `test@` addresses, leads with no email *and* no website, and leads with no stated reason to make contact. A pipeline of invented prospects produces confident forecasts and zero cash, so it never gets one.

Acquisition then researches each lead with live search and drafts a specific pitch for your approval. It declines to write anything when the research turns up nothing concrete — a generic pitch is worse than no pitch.

**Read [docs/where-to-sell.md](docs/where-to-sell.md) first — it is worth more than any feature in this repo.** The same scoped build pays $242 on an INR-denominated board and $1,800–$5,000 from a US client: a 21× difference in how many deals you need, decided entirely by who you invoice.

**Then [docs/channel-math.md](docs/channel-math.md) for the pricing and clock.** Reaching $5,000 through bid boards needs 400–3,000 competitive bids against boards that carry six postings; direct outreach needs seven conversations. [seeds/pitch.md](seeds/pitch.md) is the outreach that follows from that.

## Automated bidding

```bash
mkdir -p .earner/inbox/boards
echo '{"urls": ["https://www.freelancer.com/jobs/ai-agents"]}' > .earner/inbox/boards/default.json
earner run --approve cli
```

The bidder scans each board, scores every posting, and drafts bids only for the ones worth an hour of your time. Drafts land in `outbox/bid-*.md` with the submit URL and amount.

### Upwork — authorised discovery over the official API

```bash
export UPWORK_CLIENT_ID=... UPWORK_CLIENT_SECRET=...
earner connect --channel upwork
```

Runs the OAuth2 authorization-code flow against Upwork's own endpoints (taken from their [python-upwork-oauth2 SDK](https://github.com/upwork/python-upwork-oauth2)), stores the token at mode 0600, refreshes it automatically, and then **proves the grant with a real job search** before calling it connected. Discovered postings feed straight into the scorer. Get a key at [upwork.com/developer/keys/apply](https://www.upwork.com/developer/keys/apply).

**It drafts; it does not submit — on any board.** Upwork's public GraphQL API exposes **no mutation for submitting a proposal or spending Connects**. That is deliberate on their part: bid spam is the platform's biggest trust problem. Freelancer has no public bid API either. Anything advertising "auto-submit proposals" is going around the official API, and the cost of that is your account. `submit_proposal()` exists solely to raise an error explaining this.

Scoring is plain arithmetic, not a model judgement, because the two facts that decide whether a bid is winnable are countable:

| Rule | Why |
|---|---|
| Skip over 45 bids | Per-bid win rate falls under ~2% — a lottery ticket, not a pipeline |
| Skip under $150 top-of-range | The bid costs more to write than it pays |
| Skip when no budget is stated | Cannot price it without guessing |
| Bid at 85% of the client's range | On these boards the low bid signals the acceptance criteria went unread |
| Convert currency before comparing | A ₹37,500 ceiling is ~$390, not $37,500 |

Against the six live postings captured in `seeds/`, it bids on two and refuses four.

## Full autopilot

```bash
earner autopilot --interval 900 --autonomous --approve auto
```

One command runs the entire firm on a loop: pull Gmail → pitch → quote → invoice → collect → produce → QA → deliver → post → chase, then report and repeat. `--autonomous` lets the product agent research and pick its own niches when no brief is queued, and the growth agent source its own topics.

It stops itself, on any of four conditions:

| Stop | Meaning |
|---|---|
| `target_hit` | Settled cash reached the target. |
| `deadline` | The target's clock ran out. |
| `spend_guard` | Token spend outran revenue by more than 3× — pricing or demand is wrong, and looping harder won't fix it. |
| `max_cycles` | Whatever you set with `--max-cycles`. |

An autonomous system with no stop condition is how money leaks, so there isn't a mode without one.

## Talking to the CEO

```
$ earner ceo "where are we against the target?"

ceo> $0 settled against $5,000. Four invoices out for $1,840 — that's the whole
     gap right now, and none of it is cash until it clears. Collections is on the
     two that are past 10 days.

  directives issued:
    → collections: escalate invoice sbx_9f2 to final notice
    → acquisition: the two highest-value leads are unworked; pitch both today
  needs you:
    ! Gmail isn't connected — 3 replies are queued in the outbox and can't send
```

The CEO reads the ledger, not its own optimism. It cannot report revenue that has not settled.

## The rules the code enforces

1. **Revenue means settled.** Only a provider-confirmed payment enters the ledger as revenue, and settlement is idempotent on the provider's event id — a replayed webhook cannot double-count a dollar.
2. **Nothing reaches a customer unapproved.** Quotes, invoices, messages, and deliverables each pass an approval gate. The default gate approves nothing; it parks the job.
3. **Unprofitable work is declined.** Each quote is checked against the estimated token cost of producing it before the job is accepted.
4. **Spend is capped per job.** `EARNER_MAX_LLM_COST_CENTS` stops a runaway job at a known number.
5. **Prepay by default.** No work is done on spec, so an unpaid client cannot consume tokens.

## What this cannot do

It cannot create demand out of nothing. With no leads, no inbound mail, and no audience, it will run cleanly and earn exactly $0 — and the CEO will tell you that in those words. What it does is remove the labour between demand and cash: scoping, pricing, invoicing, producing, checking, chasing.

The $5,000/50h target is arithmetic, not a promise: `earner target` tells you how many deals at your average price that implies, and how many real leads that needs at a 30% close rate. Whether those leads exist is the part the software cannot supply.

## Tests

```bash
python -m pytest
```

Covers the money path end to end: invoicing is not revenue, settlement is idempotent, no work happens before payment, unprofitable jobs are refused, the default gate blocks outbound, and QA sends bad work back.

# Firm platform

Multi-agent services firm: discover → qualify → propose → approve → deliver →
review → invoice → settled cash. Built on the existing `earner` package.

## What works

- Opportunity pipeline with enforced state transitions and duplicate prevention
- Nine bounded agent roles + temporary co-agents (max concurrent configurable, default 4)
- Standing authorization checks and mandatory approval for submissions/contracts
- Vertical slice demo with labeled SAMPLE data and SIMULATED sandbox settlement
- Independent review before acceptance; isolated delivery workspaces
- Encrypted payout configuration (owner re-auth required; agents cannot write)
- Hourly pipeline review with bottleneck diagnosis and experiments
- Global pause + cost circuit breaker
- Dashboard (Next.js) + Python HTTP API
- Honest integration status (mock vs live)

## Run

```bash
# Install
pip install -e ".[dev]"
cd dashboard && npm install && cd ..

# End-to-end vertical slice (no external credentials required)
earner firm slice

# API (control plane)
earner firm serve --port 8787

# Dashboard (separate terminal)
cd dashboard
cp .env.local.example .env.local   # FIRM_API_URL=http://127.0.0.1:8787
npm run dev
# open http://localhost:3000
```

Owner payout secret for local/dev: set `FIRM_OWNER_SECRET` (default `owner-dev-secret`).
Payout encryption key: optional `FIRM_PAYOUT_KEY`.

## Targets (USD)

| Target | Value | Meaning |
|---|---|---|
| Aspirational rate | $1,000 / hour | Reporting target only — not spawn or spend authorization |
| First milestone | $20,000 | Cumulative provider-confirmed settled cash |

Gross revenue, costs, net contribution, and settled cash are tracked separately.
Sandbox `mark_paid` settlements are labeled **SIMULATED**.

## Supported delivery

Websites, landing pages, approved workflow automation, narrowly scoped software fixes.

## Remaining live-integration requirements

1. **Stripe** — `STRIPE_API_KEY` + `EARNER_MODE=live` for real invoices; configure webhook signature verification endpoint for production settlement events.
2. **Upwork OAuth** — `UPWORK_CLIENT_ID` / `SECRET` for discovery only; proposal submit remains manual handoff.
3. **Owner payout onboarding** — enter bank/UPI via dashboard payout screen after setting `FIRM_OWNER_SECRET`; prefer Stripe Connect for USD (UPI is typically INR-domestic).
4. **Gmail** — optional inbound briefs via existing `earner connect`.
5. **Standing authorizations** — configure per platform/service via `POST /api/standing-auth` before unattended external actions.
6. **LLM** — `ANTHROPIC_API_KEY` for generative proposal/copy paths; vertical slice uses deterministic builders so demos run without it.

## Next concrete step toward first paying customer

1. Run `earner watch --once` to surface fresh Freelancer RSS postings under your skills.
2. For each fit ≥ 0.35 opportunity, run qualify + propose in the firm (`earner firm import` then dashboard approvals).
3. Paste the approved proposal manually on the board within the first hour (no official submit API).
4. When you win, mark won / run delivery in the firm, invoice via Stripe sandbox then live.
5. Target 20–30 tailored proposals on $100–$500 fixed scopes for the first review-producing contract.

# Firm platform — assumptions and decisions

Built on the existing `earner` Python package (SQLite ledger, approval gates,
sandbox/Stripe payments, Upwork discovery, Freelancer RSS watch). This document
records engineering choices for the multi-agent firm layer.

## Stack

| Choice | Why |
|---|---|
| Extend Python `earner`, do not rewrite | Ledger, settlement idempotency, and approval gates already enforce the money rules |
| SQLite for firm state | Same durability model as the ledger; no Postgres dependency for a single-owner deployment |
| Stdlib HTTP API + Next.js dashboard | Typed UI without coupling the control plane to a Node runtime |
| File-backed job queue | Durable enough for single-process workers; restart-safe via idempotency keys |
| Provider-neutral LLM wrapper | Reuses `earner.llm`; structured outputs via JSON schemas |

## Business identity

- One verified owner. Agents are internal workers, not fake freelancer personas.
- Supported deliverables only: websites, landing pages, approved workflow
  automation, narrowly scoped software fixes.
- Explicitly out of scope until credentials exist: model training, infra ops,
  account rental, fake reviews, CAPTCHA bypass, unauthorized scraping.

## Money (USD)

| Metric | Meaning |
|---|---|
| Gross revenue | Provider-confirmed payments only |
| Costs | LLM + platform fees accrued per task |
| Net contribution | Gross − costs |
| Settled cash | Same as gross here until payout settlement is wired |
| Aspirational rate | $1,000 / hour — a target, not a spawn or spend authorization |
| First milestone | $20,000 cumulative settled cash |

Invoices, promises, and simulated sandbox marks are never shown as collected
revenue without a clear **SIMULATED** label.

## Autonomy bounds

- Default: approval required for external actions (proposals, submissions,
  invoices, spending, contracts).
- Standing authorizations are owner-configured: platform, service, price,
  daily volume, spending limits.
- Max concurrent agents: 4 (configurable). Co-agents need hypothesis, cost
  limit, and expiry.
- Global pause, cost circuit breaker, queue limits, and auto-suspend when
  authorization or budgets are missing.

## Integrations status at build time

| Integration | Status |
|---|---|
| Sample / labeled opportunity import | Live (local) |
| Freelancer public RSS watch | Live (read-only) |
| Upwork job search | Live when OAuth credentials present; otherwise mocked |
| Proposal submission (Upwork/Freelancer) | Manual handoff only — no official submit API |
| Stripe invoices | Sandbox by default; live when `EARNER_MODE=live` + key |
| Payout bank/UPI config | Encrypted local store; owner UI only; agents cannot write |
| Gmail / Instagram | Existing connectors; optional |

## Security

- Untrusted inputs (listings, attachments, repos) cannot change permissions.
- Generated code runs only in isolated workspaces under the firm deliverables
  root — never in the control-plane process with elevated rights.
- Secrets redacted in audit logs. Payout details never enter agent prompts.
- Agents cannot grant themselves permissions, change beneficiaries, or spawn
  unboundedly.

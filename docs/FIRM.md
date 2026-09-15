# Firm platform (live-first)

Real board discovery → qualify → propose → owner approve → deliver → Stripe invoice
→ provider-confirmed settled cash. Sandbox revenue is refused.

## Go live (required)

```bash
pip install -e ".[stripe,dev]"

# Create .env (never commit):
EARNER_MODE=live
STRIPE_API_KEY=sk_live_...          # or sk_test_... first
STRIPE_WEBHOOK_SECRET=whsec_...     # Dashboard → Webhooks → invoice.paid
FIRM_OWNER_SECRET=choose-a-strong-secret
```

Complete Stripe account verification and add your **USD bank payout** in the
Stripe Dashboard. Do not put bank/UPI numbers in source code.

Webhook endpoint (point Stripe at your public URL):

```
POST /api/webhooks/stripe
events: invoice.paid, invoice.payment_succeeded
```

## Run

```bash
earner firm ready                 # what is still blocked
earner firm sweep                 # live Freelancer RSS → qualify → draft proposals
earner firm serve --port 8787     # API + Stripe webhook
cd dashboard && npm install && npm run dev
```

Dashboard actions:
- **Sweep live boards** — real postings
- **Collect Stripe payments** — poll provider confirmation
- **Approve** proposals before you paste them on the board (manual submit)

## Money rules

| Counts as revenue | Does not |
|---|---|
| Stripe `invoice.paid` / settlement poll | Sandbox `mark_paid` |
| Provider event id (idempotent) | Screenshots, promises, generated invoices |

## Still manual

Upwork/Freelancer **proposal submit** has no official API — you paste approved
drafts. That is intentional and keeps the account alive.

# Grey Quantum Commerce

Public product storefront and agent-operated arbitrage loop under **CEO Anestasis Grey**
(Quantum Brain command). Built on the existing `earner` firm platform.

## What is real vs aspirational

| Item | Status |
|---|---|
| Storefront UI (India + world shipping UX) | **Real pages** in `dashboard/` |
| Agent teams A–G + CEO command cycle | **Real orchestration** (`earner commerce run`) |
| Product pages + unpaid order reservations | **Real** |
| Settled revenue | **Only after** Stripe/Razorpay/Payoneer provider confirmation |
| Daily $10k / monthly $500k / $1B vision | **Targets only** — not forecasts or guarantees |
| Meta / YouTube / Instagram ads | **Drafts** until owner approves spend + ad accounts |
| Cold DMs / call scripts | **Drafts** — never auto-sent |
| Indian Kotak / UPI bank payout | **Not configured yet** — owner adds when ready |

## Revenue model (honest)

1. **Find a loop** — buy low (wholesale / public B2B), ship, sell higher on Grey Quantum.
2. **Verify margin** — sell − source − shipping − payment fees must clear the floor.
3. **Publish page** — custom HTML + Next product route.
4. **Draft ads** — Meta, YouTube, Instagram creatives with $0 spend cap by default.
5. **Checkout** — customer reserves an unpaid order.
6. **Collect** — live payment provider confirms → mark paid with `provider_ref`.
7. **Payout** — Payoneer/Wise/Stripe India → Kotak (see `docs/INDIA-PAYOUTS.md`).

Seed catalog prices are **research hypotheses**. Re-verify suppliers before live ads.

## Commands

```bash
# Boot teams, draft pages, draft ads (no publish)
earner commerce run

# Same cycle and publish product pages
earner commerce run --publish

# CEO status / snapshot / directive
earner commerce status
earner commerce snapshot
earner commerce direct --text "Prioritize fitness niche for India shipping"

# API (with firm server)
earner firm serve --host 127.0.0.1 --port 8787
cd dashboard && npm run dev   # http://localhost:3000
```

### Owner-authenticated API

- `POST /api/commerce/run` — `{ "publish": true }`
- `POST /api/commerce/directive` — `{ "directive": "..." }`
- `POST /api/commerce/orders/mark-paid` — `{ "order_id", "provider", "provider_ref" }`

### Public API

- `GET /api/commerce`
- `GET /api/commerce/products?published=1`
- `GET /api/commerce/ceo`
- `POST /api/commerce/checkout` — unpaid reservation only

## When to add the Indian bank account

Tell the operator / this agent when you have:

1. **Payoneer or Wise** receiving account (recommended first), **or**
2. **Stripe India** merchant account for INR, **or**
3. **Razorpay** keys for UPI/cards in India

Then configure the encrypted payout screen (`/ops` → payouts) with `provider=payoneer|wise|razorpay`
and Kotak IFSC + account holder. **Do not paste account numbers into chat or git.**

Until then, the store can still take unpaid reservations and draft marketing — it cannot
truthfully show settled INR/USD cash.

## Deploy

Local/dev:

```bash
pip install -e ".[dev]"
earner firm serve --port 8787 &
cd dashboard && npm ci && npm run build && npm start
```

Production host needs: `EARNER_MODE=live`, `STRIPE_API_KEY` or Razorpay keys,
`FIRM_OWNER_SECRET`, `FIRM_PAYOUT_KEY`, and a public URL for the Next app.
Public cloud deploy (Vercel/Fly/etc.) requires owner account credentials — ask before spending.

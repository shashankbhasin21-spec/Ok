# Commercial report — evidence only

Generated from live firm DB (`/tmp/firm-live`) and code rules. No simulated
customers, deals, invoices, or payments are counted.

## Final report (USD)

| Metric | Amount |
|---|---|
| **Signed bookings** | **$0.00** |
| **Verified customer payments** | **$0.00** |
| **Net recorded cash** | **$0.00** |
| **Provider-confirmed bank payouts** | **$0.00** |
| **Qualified pipeline** | **~$186,847** (board budgets / draft bids — not bookings) |
| **Proposals actually delivered** | **$0.00** (none have external submission receipts) |
| **Delivery capacity committed** | **0 / 5** concurrent slots |
| **Spend against approved budget** | **~$3.31** agent compute costs |
| **Forecast confidence** | **INFEASIBLE as a guaranteed $300k outcome** in 15 days with 0 submissions and 0 measured close rate |
| **Gap to 2026-09-30 $300k bookings target** | **$300,000** |
| **Main bottleneck** | **no_external_proposal_submissions_with_receipts** |

### Assumptions (explicit, not measured)

- No historical submit→reply or reply→sign rates (need ≥5 outcomes each).
- Illustrative “20% close ⇒ $1.5M proposed” is **not** a forecast.
- $25k–$100k enterprise closes inside 15 days are **not** assumed.

### Connector matrix

| Connector | State | Detail |
|---|---|---|
| Freelancer RSS | VERIFIED | Read-only discovery |
| Upwork OAuth | NOT_CONFIGURED | Credentials missing |
| Stripe | BLOCKED | `EARNER_MODE=live` + `STRIPE_API_KEY` required; no sandbox fallback |
| Gmail | NOT_CONFIGURED | Optional for marketplace-first path |
| Payouts | NOT_CONFIGURED | Prefer Payoneer/Wise → Kotak (US Stripe often rejects Kotak) |
| Proposal submit | BLOCKED | Manual handoff only |

### Next three evidence-backed actions

1. Approve drafted proposals; paste on Freelancer/Upwork; record `board_submission_receipt` (internal approval ≠ submitted).
2. Open Payoneer/Wise; set marketplace withdrawal (Kotak often cannot link to US Stripe).
3. Re-scope near-term offers to **$100–$500** fixed scopes for first review-producing contracts — do not plan $25k–$100k closes in this window without evidence.

### Owner inputs still required

- `EARNER_MODE=live`, `STRIPE_API_KEY`, `FIRM_OWNER_SECRET` (≥12 chars, not default)
- Payoneer/Wise account for India payouts
- Manual proposal submission on boards + paste receipt URLs into the firm

# Getting paid in India (Kotak / INR)

US Stripe often **cannot** pay out to an Indian Kotak account. That does not
block the business. Use the rails that actually settle to India.

## Recommended order (first paying customer)

### 1. Marketplace escrow (best for first jobs) — no Stripe bank needed

Freelancer.com / Upwork hold the client’s money and pay **you**:

1. Win the job (firm already drafts proposals from live boards)
2. Deliver through the platform
3. Withdraw to **Payoneer** or **Wise**, then to Kotak  
   (or to an Indian bank if the platform offers it)

This is how most India-based freelancers get the first review. Stripe is optional here.

### 2. Payoneer or Wise (direct USD clients)

| Rail | Use for |
|---|---|
| **Payoneer** | Marketplace withdrawals + some direct clients |
| **Wise** | Direct USD invoices / wires, then withdraw to Kotak |
| **Kotak SWIFT / FIRC** | Large direct wire from a US client (bank handles forex) |
| **UPI** | INR only — does **not** accept USD |

Configure the encrypted payout screen with `provider=payoneer` or `wise`.
Do not put account numbers in chat or git.

### 3. Stripe — only if the account country matches the bank

| Stripe account country | Typical payout bank |
|---|---|
| **Stripe India** | Indian banks (INR), including many Kotak accounts |
| **Stripe US / other** | Usually needs a bank in that country — Kotak often rejected |

If Kotak was rejected, you are almost certainly on a **non-India** Stripe
account. Fixes:

- Create / switch to **Stripe India** for INR clients, **or**
- Keep US Stripe but payout to a **Wise/Payoneer USD receiving account**, not Kotak directly, **or**
- Skip Stripe for now and use marketplace escrow + Payoneer

## What the firm does today

- Live job discovery and proposals: **running** (no bank required)
- Settled cash in the ledger: only after a **payment provider** confirms money
- Sandbox/demo cash: **refused**

## Next action

1. Open a **Payoneer** (or Wise) account linked to Kotak for withdrawals.  
2. On Freelancer/Upwork, set payout method to Payoneer.  
3. Keep approving firm proposals and submitting them manually on the boards.  
4. Add Stripe later only if you have a compatible payout destination (Stripe India, or Wise/Payoneer receiving details).

# Prompt to paste to Grok / next agent

Copy everything below the line into Grok (or a new Cursor agent).

---

You are continuing the live firm on GitHub `shashankbhasin21-spec/Ok`, branch `cursor/us-high-ticket-outreach-3ae6`, PR https://github.com/shashankbhasin21-spec/Ok/pull/5 (base `claude/ai-agents-real-money-bpqgqt`).

Owner Gmail: `shashankbhasin21@gmail.com` · phone on packets: `+91 7972836653`

## Read first
1. `seeds/HANDOFF-NEXT-AGENT.md` (refresh counts below — RESEARCHED is now 17)
2. `docs/REAL_OPERATIONS_PLAN.md` + `AGENTS.md`
3. `seeds/proposals/APPLY-QUEUE-2026-09-16.json`
4. `seeds/leads/euro10k-world-fresh-2026-09-16.csv`
5. `seeds/outreach-log/2026-09-16-world-fresh.json`
6. Gmail drafts to owner: subjects containing `MASTER APPLY DRAFT`, `GROK HANDOFF`, `LEADS HELD`

## Pipeline truth (do not invent)
```bash
export PATH="$HOME/.local/bin:$PATH"
python3 - <<'PY'
from earner.outreach import Pipeline
p=Pipeline('.earner/outreach.db')
print(p.summary())  # expect SENT≈17, BOUNCED=1, RESEARCHED=17
print('remaining_today', p.remaining_today())
p.close()
PY
```
- Prior EUR10k + US waves already **SENT** — never resend those emails.
- Fresh world wave is **RESEARCHED only** (held). Mail only if owner says AUTHORIZE SEND and within daily ~20 stranger cap.
- Freelancer/Upwork/Submittable: **no auto-submit**. Paste + `board_submission_receipt` only.
- **Never** email IAWF packet to `execdir@iawfonline.org`. Submittable URL: https://iawf.submittable.com/submit/364943/rfp-for-new-iawf-website — needs owner Google login. Packet: `seeds/proposals/iawf-website-submittable-2026-09-16.md`

## Your job now
1. If owner authorized: draft/send remaining RESEARCHED EUR10k emails via Gmail MCP from `shashankbhasin21@gmail.com`, then advance pipeline RESEARCHED→DRAFTED→SENT with message ids.
2. Keep IAWF + Freelancer paste packets current in MASTER APPLY DRAFT.
3. On bounce: mark BOUNCED. On reply: SENT→REPLIED→honest quote from `seeds/requirements/*`.
4. Do not claim settled cash, signed bookings, or marketplace submit without evidence.
5. Do not spawn unlimited workers; max 4; no spend without authorization.
6. Commit/push small PRs on `cursor/…-3ae6` branches; report what is still disconnected.

## Offers
- EUR 10,000 Digital Ops — `seeds/requirements/euro10k_digital_ops_package.md`
- US landing $1,800 / workflow $2,500 — `seeds/requirements/landing_page.md`, `workflow_automation.md`

Start by confirming pipeline `summary()` and listing RESEARCHED emails, then wait for owner AUTHORIZE SEND before mailing strangers.

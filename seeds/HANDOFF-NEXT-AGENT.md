# HANDOFF — resume this chat (for Grok / next agent)

**Branch:** `cursor/us-high-ticket-outreach-3ae6`  
**PR:** https://github.com/shashankbhasin21-spec/Ok/pull/4  
**Owner Gmail (all sends/drafts):** `shashankbhasin21@gmail.com`  
**Phone on packets:** `+91 7972836653`  
**Updated:** 2026-09-16T19:00Z

## What is already done (do not redo)
1. US high-ticket Gmail wave ($1.8k / $2.5k) — sent; prior batch suppressed for no-resend.
2. EUR 10k global + small-country Gmail waves — **17 SENT**, **1 BOUNCED** (`info@thalyshotel.com` domain missing).
3. Freelancer globe hunt JSON + coverage docs under `seeds/opportunities/`.
4. Paste-ready proposals under `seeds/proposals/`.
5. Master Gmail draft to owner with all paste packets (id `r780583678775568680`, subject contains `MASTER APPLY DRAFT`).
6. Grok handoff Gmail draft id `r2547786456106441221` (subject contains `GROK HANDOFF`).
7. IAWF Submittable packet saved; **do not email** `execdir@iawfonline.org`.
8. **IAWF/Submittable login check** (owner: “Iref login check”) — see below.

## IAWF / Submittable login check (2026-09-16)
| Fact | Evidence |
|---|---|
| Owner created Submittable account | Welcome email thread `1a0ab8badbfa3a11` at 18:47Z |
| Cloud browser logged in? | **No** — redirected to `accounts.submittable.com` login |
| Blocker UI | “Log in to International Association of Wildland Fire to continue to Submittable” |
| OAuth available | Continue with Google / Facebook + email+password |
| Auto-submit possible? | **No** without owner session |

Log: `seeds/outreach-log/2026-09-16-iawf-login-check.json`

## What still needs owner / login (cannot fully auto)
| Item | Action | Blocker |
|---|---|---|
| IAWF website RFP · **USD 38,000** | Paste into Submittable | Needs owner Submittable session (account exists; cloud agent not logged in) |
| Freelancer multilingual US printer | Paste bid | No Freelancer submit API |
| Freelancer UK yoga | Paste bid | No Freelancer submit API |
| Freelancer Lithuania CNC ERP | Confirm URL + paste | No submit API |

**Submittable URL:** https://iawf.submittable.com/submit/364943/rfp-for-new-iawf-website  
**IAWF packet:** `seeds/proposals/iawf-website-submittable-2026-09-16.md`  
**Apply queue JSON:** `seeds/proposals/APPLY-QUEUE-2026-09-16.json`

## Local pipeline DB (gitignored)
Path: `.earner/outreach.db`  
Stages: SENT=17, BOUNCED=1, RESEARCHED=17 (world-fresh held)  

```bash
export PATH="$HOME/.local/bin:$PATH"
python3 - <<'PY'
from earner.outreach import Pipeline
p=Pipeline('.earner/outreach.db')
print(dict(p.db.execute('select stage, count(*) from prospects group by stage')))
p.close()
PY
```

## Commands already used / re-run safely
```bash
# Install CLI
python3 -m pip install -e '.[dev]' -q
export PATH="$HOME/.local/bin:$PATH"

# Freelancer sweep (read-only)
python3 -c "from earner.watch import freelancer_sources; print(sum(len(f()) for f in freelancer_sources()))"

# Bounce check via Gmail MCP: search mailer-daemon newer_than:1d
# Outreach send via Gmail MCP send_message (from shashankbhasin21@gmail.com)
# NEVER email IAWF packet to execdir@
# Master draft: list_drafts query subject:MASTER APPLY DRAFT → id r780583678775568680
```

## Firm rules that must stay
- No mock requirements — use `seeds/requirements/*`
- Marketplace = draft + owner paste + `board_submission_receipt`
- Daily Gmail stranger cap ~20 (already heavy today — prefer draft/hold unless owner authorizes more)
- Targets ≠ spend authorization; evidence required for external outcomes

## Next agent priority
1. If owner confirms Submittable logged-in session usable → submit IAWF from packet; record confirmation ID.
2. If Freelancer session available → paste B1/B2/B3 from master draft; record receipts.
3. Otherwise: keep drafts current; check DSN/bounces; do not spam resent SENT addresses.
4. On reply → move prospect SENT→REPLIED→quote EUR/USD honestly from offer catalog.

## Key seed paths
- `seeds/GROK-PROMPT.md` — paste into Grok
- `seeds/leads/euro10k-world-fresh-2026-09-16.csv`
- `seeds/leads/*.csv`
- `seeds/proposals/*`
- `seeds/requirements/*`
- `seeds/outreach-log/*`
- `seeds/opportunities/GLOBE-HUNT-COVERAGE.md`
- `docs/REAL_OPERATIONS_PLAN.md`

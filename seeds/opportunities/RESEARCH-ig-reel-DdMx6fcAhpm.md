# Research — Instagram reel DdMx6fcAhpm

**URL:** https://www.instagram.com/reel/DdMx6fcAhpm/  
**Poster:** @atsmatrix (Anyelo Encarnacion / ATS-MATRIX)  
**Fetched:** 2026-09-16  
**Engagement (og):** ~2,461 likes · 133 comments · posted ~12 Sep 2026  

## What the reel claims
- **40 agents**, one file, autonomous loop  
- **202 requests/minute** vs human ~3  
- **$0.27 per attempt** → **$121k / month run-rate** → **$1.4M / year if the loop holds**  
- Pipeline slogan: Request → worker → gate → store → root  
- Branding: “Revenue Observatory” / ATS Matrix  

## What checks out vs what does not
| Claim | Assessment |
|---|---|
| ATS Matrix exists as a product/brand | **Yes** — IG account + third-party writeups of a single-file agent visualizer |
| “One file” architecture | **Plausible** — Starlog describes ATSMATRIX as a single HTML/JS canvas visualizer (demo-ware), not a durable ops stack |
| Orchestrates / pays real customers | **Not evidenced** — reviews say it **visualizes** agent traffic; does not replace LangGraph/CrewAI execution or ledger cash |
| $121k/mo / $1.4M/yr “run-rate” | **Marketing math, not audited cash** — extrapolates request×price; no Stripe/bank settlement, contracts, or refunds shown |
| “Never sleeps / nothing lost” | **Contradicted for the visualizer design** — Starlog notes no persistence, refresh wipes session, events can drop under load |
| Comparable to this firm’s books | **No** — our rule: settled provider receipts ≠ projected run-rate |

## Math sniff test (illustrative only)
202 req/min × 60 × 24 × 30 ≈ 8.7M attempts/month × $0.27 ≈ **$2.3M** if every request were paid — the caption’s $121k implies a much lower paid-conversion fraction or different unit. Either way, **run-rate ≠ collected cash**.

## Firm relevance (this repo)
- Do **not** treat as a template to claim $300k/mo or $2k/hr from agent count alone.  
- Keep approval gates, settled cash, and board_submission_receipt separate.  
- Spawning “40 agents” without backlog + budget evidence violates firm scaling policy.  
- Useful takeaway only: clear request→worker→gate→store loop naming — we already require evidence at each external step.

## Sources
- Instagram og/embed caption for DdMx6fcAhpm (@atsmatrix)  
- starlog.is article on ATSMATRIX agent visualizer (demo-ware critique)  
- No public audited revenue filing found for “Revenue Observatory” / ATS Matrix  

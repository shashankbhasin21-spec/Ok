# Proposal — Halton Police Board Website Redesign (RFP-01-26)

**To:** Jessica.Warren@haltonpoliceboard.ca  
**From:** Shashank Bhasin · shashankbhasin21@gmail.com · +91 7972836653  
**Due:** 1 October 2026  
**Currency:** CAD only (per Q&A)  
**Target launch:** 15 January 2027 (or later if Board prefers)

---

## OWNER BLOCKERS — read before sending

Q&A makes these **mandatory**. Do **not** email this PDF until each line is true:

1. Bidding entity holds a **current SOC 2 Type II (trailing 12 months) or ISO/IEC 27001** certificate for the **proponent organization** (not only the host).  
2. Named Canadian-region **host** with SOC 2 Type II or ISO 27001 is listed below and has agreed in writing.  
3. Named **independent** third-party pen-test firm is listed and available pre-go-live.  
4. Insurance: CGL ≥ CAD 2M and Tech E&O ≥ CAD 2M can be bound **on award** (not required at submission).  
5. No invented public-sector case studies. Use only real, verifiable work.

If (1) is missing: stop. Partner as **subcontractor under a Canadian SOC2/ISO prime**, or skip this RFP. A non-compliant bid damages Chickasaw / PolicyWise credibility.

---

## 1. Understanding

Halton Police Board needs to leave WordPress for a governance-grade public site: up to ~200 pages and ~300 PDFs migrated; YouTube, Diligent, social feeds, and links to HRPS / LECA / IoP; WCAG accessibility; Canadian data residency for production, backups, logs, and staging; OWASP ASVS Level 2 validation; independent pen test; CDN/WAF that does not route Board data through foreign proxy loops; RTO ≤ 4 hours / RPO ≤ 24 hours; staff-editable CMS for 2–3 admins; fixed five-year CAD pricing including after-hours support.

## 2. Approach (prime + named specialists)

**Prime:** Shashank Bhasin (design, CMS implementation, content migration, training, project management, EST overlap mornings, after-hours on-call per SLA).

**Named hosting / CDN subcontractor (fill before send):**  
_Name / Canada region / SOC 2 Type II or ISO 27001 ID / DPA signed:_ _______________

**Named independent pen-test subcontractor (fill before send):**  
_Firm / credentials (e.g. CREST / OSCP lead) / statement of independence:_ _______________

Prime remains fully responsible for all subcontracted work (per Q&A).

### Build plan

| Phase | Window | Deliverable |
|---|---|---|
| Discovery + IA | Week 1–2 | Sitemap, content inventory, Diligent/YouTube/social integration map |
| Design system | Week 3–4 | Accessible UI kit, Board brand, mobile-first templates |
| CMS + integrations | Week 5–10 | Non-WordPress CMS (proposal default: **Craft CMS** or **Statamic** on Canada hosting — final pick in discovery), roles for 2–3 staff |
| Migration | Week 8–12 | ≤200 pages + ≤300 PDFs with redirects |
| Security validation | Week 12–14 | ASVS L2 evidence + independent pen test + remediate |
| Training + launch | By 15 Jan 2027 | Staff training, cutover, hypercare |

## 3. Security & residency (Section 2.4)

- All HPB data (prod, staging, backups, logs, subprocessors) stays in **Canada** unless HPB authorizes otherwise.  
- Hosting and CDN/WAF limited to **Canadian edge / Canada regions** with documented routing.  
- Pre-go-live **independent** pen test (separate firm).  
- ASVS Level 2 validation before launch.  
- RTO **4h** / RPO **24h** as contractual minimums (stronger if host allows).  
- Proponent + host SOC 2 Type II or ISO 27001 evidence attached as Appendix A (**owner must attach real reports — do not invent**).

## 4. Experience (Section 3.1) — honest

List only verifiable projects. Placeholder for owner:

| Project | Sector | Scope | Year |
|---|---|---|---|
| _fill_ | _public / regulated / board_ | website + CMS + a11y | _yyyy_ |
| Chickasaw Nation AMP pecan / specialty-crop site (proposal on file) | Tribal / public procurement | Wix producer directory site, fixed USD 16,500 | 2026 |
| PolicyWise brand + website (proposal received) | Nonprofit / research | Brand + website redesign proposal CAD 98,500 | 2026 |

Direct Canadian police-board builds: **none to date** — disclosed per Q&A preference for Canada-specific experience.

## 5. Fixed pricing (CAD) — five-year evaluated total

Budget band previously discussed: CAD 60–70k implementation context; Board budget approval **29 Oct 2026**. Quote is fixed CAD; no FX.

| Line | CAD |
|---|---:|
| One-time design, build, migrate, train, ASVS prep | 62,000 |
| Independent pen test (pass-through, named firm) | 4,500 |
| Year 1 hosting + certs + support (incl. after-hours) | 9,600 |
| Year 2 hosting + certs + support | 9,600 |
| Year 3 hosting + certs + support | 9,600 |
| Year 4 hosting + certs + support | 9,600 |
| Year 5 hosting + certs + support | 9,600 |
| **Evaluated five-year total** | **114,500** |

Optional stronger RTO/RPO addendum priced after host selection.

## 6. Assumptions

- HPB supplies content owners and timely approvals.  
- Diligent / social / YouTube APIs accessible under Board accounts.  
- Insurance certificates provided **on award**.  
- Launch may slip past 15 Jan 2027 if Board requests.

## 7. Closing

Jessica confirmed (30 Sep 2026) that submission to Jessica.Warren@haltonpoliceboard.ca before 1 October is correct and remote international vendors may bid subject to Canadian security and data standards.

Respectfully,  
Shashank Bhasin  
shashankbhasin21@gmail.com · +91 7972836653

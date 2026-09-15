# Shared Codex / Cursor development rules

The owner's goal is a live automated service business targeting USD 300,000 per
calendar month and USD 2,000/hour per agent. This is a target, never a promise or authorization to spend.
Read docs/REAL_OPERATIONS_PLAN.md before changing firm behavior.

- Work on separate branches; inspect upstream changes before merging. Never
  overwrite another agent's edits. Keep PRs small and independently testable.
- Production must never silently fall back to sandbox or show sample receipts as
  cash. Credentials alone do not prove that an integration works.
- Keep signed bookings, invoices, customer receipts, refunds, fees and bank
  payouts separate. Preserve currencies and source receipts; never invent FX.
- Approval is not submission, internal review is not customer acceptance, and
  a task row is not a running worker. Require evidence for external outcomes.
- Automate read-only research, drafting and tests within configured limits.
  Outreach, submissions, contractual commitments, spending and deployment need
  applicable owner authorization. Do not broaden an existing authorization.
- Start with at most four workers. Scale only within explicit budgets when
  measured backlog and delivery capacity justify it; never chase a target by
  spawning unlimited agents.
- Never commit credentials, bank details or customer secrets. Missing required
  security configuration must fail closed.
- Run meaningful regression tests for changed behavior and report what remains
  disconnected. Do not claim production readiness from unit tests alone.

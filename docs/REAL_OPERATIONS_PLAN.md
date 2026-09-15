# Live firm execution plan

Owner objective: USD 300,000 per month from real customer work. The dashboard
measures calendar months in UTC. The owner also requests a USD 2,000/hour
per-agent target and parallel specialist teams. Start without assumed revenue;
zero starting cash must not imply permission to incur costs or debt. Revenue is not guaranteed; a target does not
supply buyers, credentials, permissions or delivery capacity.

## This branch

- Separate sandbox receipts from non-sandbox USD receipts and outstanding invoices.
- Show the monthly USD 300,000 target and recorded collections separately.
- Stop reporting integrations as live just because environment variables exist.
- Stop silently replacing a failed payment provider with a sandbox provider.
- Require an explicit payout password, vetted encryption and an external Fernet
  key; expire payout reauthentication after five minutes.

These are foundations, not proof that the business is autonomous or earning.
Recorded non-sandbox receipts still depend on ingestion correctness. Provider test
mode, refunds, chargebacks, bank payouts and accounting recognition need further
work before using these figures as audited income. Agent costs currently include
synthetic charges; net contribution is not verified profit.

## Next implementation gates for Codex and Cursor

1. Secure the control plane. Authenticate every private read and mutation,
   constrain CORS, isolate previews, remove arbitrary file import, add request
   limits, and disable simulation actions in live mode. Do not expose the current
   API publicly before this gate passes.
2. Verify connectors with read-only account checks and timestamped results.
   Record provider account ID, live/test mode, granted scopes and last failure.
   Missing access must leave the connector blocked, never fake a successful run.
3. Implement persistent worker execution: leases, heartbeats, bounded retries,
   idempotent external operations, crash recovery, cancellation and spend limits.
   Worker status must derive from execution, not only database rows.
4. Build the real sales loop: fresh attributable opportunities, qualification,
   scoped offers, owner-authorized outreach, actual submission receipts, buyer
   replies and signed acceptance. Prioritize website projects and feasible AI
   integrations; never promise unavailable training infrastructure or experience.
5. Build the delivery loop: isolated workspaces, actual model calls with measured
   usage costs, project-specific implementation, independent verification and
   customer acceptance evidence. A generated template is not completed delivery.
6. Build the money loop: approved invoice creation, signed payment webhooks or
   authenticated reconciliation, idempotency, live/test separation, refunds,
   disputes, fees, currency ledgers and bank payout reconciliation.
7. Prove the complete flow on one authorized real customer project before
   scaling. Report funnel conversion, project margin, turnaround and receivables.
   Expand workers only for an evidenced bottleneck inside owner-set budgets.

## Required operational configuration

Provider accounts and scoped credentials, verified business/payment identity,
service offerings and delivery capacity, authorized channels and recipients,
pricing and contract limits, daily/monthly spend caps, deployment destination and
operator access. Implement unblocked code without inventing these inputs.

## Collaboration

Use independent branches and reviewable PRs. The other agent may be working on
adjacent files; fetch and compare before integration. Neither tool should claim
the other is running a task without direct evidence. Report completed code,
verified runtime behavior and unresolved integrations separately.

## Scaling policy to implement

Missing an earnings target alone must never trigger recursive spawning. Scale by
at most one worker per review when an executable backlog exists, delivery
capacity is the measured bottleneck, owner concurrency/spend limits allow it,
and the additional work has an evidenced positive contribution margin. Otherwise
record the blocker and improve qualification, offer or delivery. If the available
budget is zero, use only authorized zero-cost operations and stop before paid
model calls. Record real per-agent billed work and attributable collected cash;
do not divide aggregate cash arbitrarily to claim an agent earned its target.

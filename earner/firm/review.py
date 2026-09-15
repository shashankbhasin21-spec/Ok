"""Hourly pipeline review — bottleneck diagnosis and controlled experiments."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from . import (
    ASPIRATIONAL_RATE_CENTS_PER_HOUR,
    HOURLY_REVIEW_SECONDS,
    MILESTONE_CENTS,
    MONTHLY_CASH_TARGET_CENTS,
)
from .agents import ExperimentAnalyst
from .authorization import Authorization
from .coordinator import Coordinator
from .store import FirmStore


def pipeline_metrics(store: FirmStore) -> dict:
    opps = store.list_opportunities()
    by_status: dict[str, int] = {}
    for o in opps:
        by_status[o.status] = by_status.get(o.status, 0) + 1

    submitted = by_status.get("submitted", 0) + by_status.get("replied", 0) + by_status.get("won", 0) + by_status.get("lost", 0)
    replied = by_status.get("replied", 0) + by_status.get("won", 0) + by_status.get("lost", 0)
    won = sum(by_status.get(s, 0) for s in ("won", "delivery", "review", "accepted", "invoiced", "paid"))

    app_to_reply = (replied / submitted) if submitted else None
    reply_to_win = (won / replied) if replied else None

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_end = (
        month_start.replace(year=now.year + 1, month=1)
        if now.month == 12
        else month_start.replace(month=now.month + 1)
    )
    monthly_cash = store.gross_revenue_cents(
        since=month_start.timestamp(), until=month_end.timestamp()
    )
    gross = store.gross_revenue_cents()
    simulated = store.gross_revenue_cents(simulated=True)
    costs = store.total_agent_cost_cents()
    invoiced = store.invoiced_cents()

    # Measurement window: hours since first opportunity or 1h minimum.
    if opps:
        oldest = min(o.created_at for o in opps)
        window_h = max(1 / 60, (time.time() - oldest) / 3600)
    else:
        window_h = 0.0
    observed_rate = (gross / window_h) if window_h > 0 else 0.0

    return {
        "by_status": by_status,
        "counts": {
            "opportunities": len(opps),
            "submitted": submitted,
            "replied": replied,
            "won": won,
        },
        "conversion": {
            "application_to_reply": app_to_reply,
            "reply_to_win": reply_to_win,
        },
        "finance_usd": {
            "gross_revenue_cents": gross,
            "simulated_receipts_cents": simulated,
            "monthly_target_cents": MONTHLY_CASH_TARGET_CENTS,
            "monthly_settled_cash_cents": monthly_cash,
            "monthly_target_progress": monthly_cash / MONTHLY_CASH_TARGET_CENTS,
            "month_utc": month_start.strftime("%Y-%m"),
            "invoiced_outstanding_cents": invoiced,
            "costs_cents": costs,
            "net_contribution_cents": gross - costs,
            "settled_cash_cents": gross,
            "milestone_cents": MILESTONE_CENTS,
            "milestone_progress": gross / MILESTONE_CENTS if MILESTONE_CENTS else 0,
            "aspirational_rate_cents_per_hour": ASPIRATIONAL_RATE_CENTS_PER_HOUR,
            "observed_rate_cents_per_hour": int(observed_rate),
            "measurement_window_hours": round(window_h, 3),
        },
        "commercial": store.commercial_snapshot(),
        "note": (
            "Zero settled cash is not business failure — distinguish buyer response time, "
            "delivery time, and payment settlement delays. Bookings require signed evidence."
            if gross == 0
            else "Recorded USD receipts exclude sandbox payments. Bank payouts and refunds are not reconciled here."
        ),
    }


def run_hourly_review(coordinator: Coordinator) -> dict:
    """Evaluate the pipeline. Spawn at most one co-agent for a measured bottleneck."""
    store = coordinator.store
    auth = Authorization(store)
    metrics = pipeline_metrics(store)

    analyst = ExperimentAnalyst(store, auth, workdir=coordinator.workdir)
    result = analyst.run()
    bottleneck = result.output.get("bottleneck", "unknown")
    experiment_id = result.output.get("experiment_id")

    co_agent_id = None
    # Only spawn a co-agent when it addresses a specific bottleneck within budget.
    if result.ok and bottleneck not in ("payment_settlement",) and not store.is_paused():
        try:
            auth.assert_can_spawn()
            run = coordinator.spawn_co_agent(
                hypothesis=result.output.get("hypothesis", ""),
                deliverable=f"address bottleneck: {bottleneck}",
                cost_limit_cents=150,
                expires_in_sec=HOURLY_REVIEW_SECONDS,
            )
            co_agent_id = run["id"]
            if experiment_id:
                store.conn.execute(
                    "UPDATE experiments SET co_agent_id=? WHERE id=?",
                    (co_agent_id, experiment_id),
                )
                store.conn.commit()
        except Exception:  # noqa: BLE001 - review must complete even if spawn refused
            co_agent_id = None

    stopped = coordinator.reap_expired()
    review = store.record_review_cycle(
        bottleneck=bottleneck,
        findings={
            "metrics": metrics,
            "experiment": result.output,
            "co_agent_id": co_agent_id,
            "stopped_agents": stopped,
            "zero_revenue_guidance": (
                None
                if metrics["finance_usd"]["gross_revenue_cents"]
                else {
                    "do_not_assume_failure": True,
                    "check": [
                        "buyer_response_time",
                        "delivery_time",
                        "payment_settlement_delay",
                    ],
                }
            ),
        },
        experiment_id=experiment_id,
    )
    return {
        "review_id": review["id"],
        "bottleneck": bottleneck,
        "experiment_id": experiment_id,
        "co_agent_id": co_agent_id,
        "metrics": metrics,
        "stopped_agents": stopped,
    }

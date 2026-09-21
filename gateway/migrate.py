"""Safe schema migrations / backfills for existing SQLite/Postgres databases."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_COUNTER_COLUMNS = (
    "success_count",
    "failure_count",
    "qc_failure_count",
    "total_render_time_sec",
    "total_queue_latency_sec",
)


def backfill_engine_metric_counters(engine: Engine) -> None:
    """Convert NULL metric counters to 0 before NOT NULL enforcement matters.

    Existing production DBs may already contain NULL values from earlier schema
    versions. This is idempotent and safe to run on every startup.
    """
    with engine.begin() as conn:
        # Ensure table exists (create_all may run before or after).
        try:
            conn.execute(text("SELECT 1 FROM engine_metrics LIMIT 1"))
        except Exception:  # noqa: BLE001
            return

        for col in _COUNTER_COLUMNS:
            try:
                conn.execute(
                    text(
                        f"UPDATE engine_metrics SET {col} = 0 WHERE {col} IS NULL"
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("counter backfill skipped for %s: %s", col, exc)

        # Best-effort NOT NULL enforcement on SQLite is limited without rebuild;
        # application code treats NULL as 0 regardless.
        logger.info("engine_metrics counter backfill complete")

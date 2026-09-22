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


def dedupe_engine_metrics(engine: Engine) -> int:
    """Merge duplicate engine_metrics rows (same engine name) into one survivor.

    Production failure mode: SQLAlchemy MultipleResultsFound on .one_or_none()
    when legacy inserts created more than one row per engine. Idempotent.
    Returns number of duplicate rows deleted.
    """
    with engine.begin() as conn:
        try:
            conn.execute(text("SELECT 1 FROM engine_metrics LIMIT 1"))
        except Exception:  # noqa: BLE001
            return 0

        engines = [
            row[0]
            for row in conn.execute(
                text("SELECT engine FROM engine_metrics GROUP BY engine HAVING COUNT(*) > 1")
            ).fetchall()
        ]
        deleted = 0
        for eng in engines:
            rows = conn.execute(
                text(
                    "SELECT id, success_count, failure_count, total_render_time_sec, "
                    "total_queue_latency_sec, qc_failure_count FROM engine_metrics "
                    "WHERE engine = :e ORDER BY id ASC"
                ),
                {"e": eng},
            ).fetchall()
            if len(rows) < 2:
                continue
            keep_id = rows[0][0]
            success = sum(int(r[1] or 0) for r in rows)
            failure = sum(int(r[2] or 0) for r in rows)
            render = sum(float(r[3] or 0) for r in rows)
            queue = sum(float(r[4] or 0) for r in rows)
            qc = sum(int(r[5] or 0) for r in rows)
            conn.execute(
                text(
                    "UPDATE engine_metrics SET success_count = :s, failure_count = :f, "
                    "total_render_time_sec = :r, total_queue_latency_sec = :q, "
                    "qc_failure_count = :qc WHERE id = :id"
                ),
                {"s": success, "f": failure, "r": render, "q": queue, "qc": qc, "id": keep_id},
            )
            drop_ids = [r[0] for r in rows[1:]]
            for did in drop_ids:
                conn.execute(text("DELETE FROM engine_metrics WHERE id = :id"), {"id": did})
            deleted += len(drop_ids)
            logger.warning(
                "deduped engine_metrics engine=%s kept_id=%s removed=%s",
                eng,
                keep_id,
                drop_ids,
            )

        # Best-effort unique index so duplicates cannot return.
        try:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_engine_metrics_engine "
                    "ON engine_metrics (engine)"
                )
            )
        except Exception as exc:  # noqa: BLE001
            # Postgres may need different concurrent DDL; ignore if unsupported.
            logger.warning("unique index on engine_metrics.engine skipped: %s", exc)

        if deleted:
            logger.info("engine_metrics dedupe removed %s duplicate row(s)", deleted)
        return deleted

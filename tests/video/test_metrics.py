"""Regression tests for NULL-safe engine metric counters."""

from __future__ import annotations

from gateway.metrics import _inc, record_engine_result
from gateway.models import EngineMetric


def test_inc_null_safe():
    assert _inc(None, 1) == 1
    assert _inc(0, 1) == 1
    assert _inc(4, 2) == 6
    assert _inc(None, 1.5) == 1.5


def test_null_failure_count_increments_to_one(client):
    """NULL-safe increment: None + 1 → 1 (legacy row simulation)."""
    assert int(_inc(None, 1)) == 1
    import gateway.database as dbmod

    with dbmod.SessionLocal() as db:
        # Create with zeros, then apply null-safe update as production code does.
        db.add(
            EngineMetric(
                engine="ltx",
                success_count=0,
                failure_count=0,
                total_render_time_sec=0.0,
                total_queue_latency_sec=0.0,
                qc_failure_count=0,
            )
        )
        db.commit()
        row = db.query(EngineMetric).filter(EngineMetric.engine == "ltx").one()
        legacy_failure = None  # as loaded from old DB
        row.failure_count = int(_inc(legacy_failure, 1))
        db.commit()
        assert row.failure_count == 1


def test_null_success_count_increments_to_one(client):
    assert int(_inc(None, 1)) == 1
    import gateway.database as dbmod

    with dbmod.SessionLocal() as db:
        db.add(
            EngineMetric(
                engine="wan",
                success_count=0,
                failure_count=0,
                total_render_time_sec=0.0,
                total_queue_latency_sec=0.0,
                qc_failure_count=0,
            )
        )
        db.commit()
        row = db.query(EngineMetric).filter(EngineMetric.engine == "wan").one()
        legacy_success = None
        legacy_render = None
        row.success_count = int(_inc(legacy_success, 1))
        row.total_render_time_sec = float(_inc(legacy_render, 12.5))
        db.commit()
        assert row.success_count == 1
        assert float(row.total_render_time_sec) == 12.5


def test_existing_counter_increments_normally(client):
    import gateway.database as dbmod

    with dbmod.SessionLocal() as db:
        db.add(
            EngineMetric(
                engine="framepack",
                success_count=2,
                failure_count=3,
                total_render_time_sec=10.0,
                total_queue_latency_sec=1.0,
                qc_failure_count=1,
            )
        )
        db.commit()

    with dbmod.SessionLocal() as db:
        record_engine_result(db, "framepack", success=False, qc_failed=True)
        row = db.query(EngineMetric).filter(EngineMetric.engine == "framepack").one()
        assert row.failure_count == 4
        assert row.success_count == 2
        assert row.qc_failure_count == 2


def test_metrics_failure_does_not_raise(client, monkeypatch):
    """Bookkeeping errors must be swallowed so job path survives."""
    import gateway.database as dbmod

    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise RuntimeError("db broke")

    monkeypatch.setattr("gateway.metrics._record_engine_result_unsafe", boom)
    with dbmod.SessionLocal() as db:
        record_engine_result(db, "ltx", success=False)  # must not raise
    assert calls["n"] == 1


def test_duplicate_engine_metrics_do_not_break_routing(client):
    """Legacy duplicate rows must not raise MultipleResultsFound."""
    import gateway.database as dbmod
    from sqlalchemy import text

    from gateway.metrics import get_engine_metric_row, record_engine_result
    from gateway.router import _hist_stats

    with dbmod.engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS uq_engine_metrics_engine"))

    with dbmod.SessionLocal() as db:
        for _ in range(3):
            db.add(
                EngineMetric(
                    engine="ltx",
                    success_count=1,
                    failure_count=1,
                    total_render_time_sec=10.0,
                    total_queue_latency_sec=1.0,
                    qc_failure_count=0,
                )
            )
        db.commit()
        # Would raise with .one_or_none(); must succeed with helper.
        row = get_engine_metric_row(db, "ltx")
        assert row is not None
        stats = _hist_stats(db, "ltx")
        assert 0.0 <= stats["success_rate"] <= 1.0
        record_engine_result(db, "ltx", success=True, render_time_sec=2.0)


def test_dedupe_engine_metrics_merges_and_indexes(client):
    import gateway.database as dbmod
    from sqlalchemy import text

    from gateway.migrate import dedupe_engine_metrics

    with dbmod.engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS uq_engine_metrics_engine"))

    with dbmod.SessionLocal() as db:
        db.add(
            EngineMetric(
                engine="wan",
                success_count=2,
                failure_count=1,
                total_render_time_sec=5.0,
                total_queue_latency_sec=1.0,
                qc_failure_count=0,
            )
        )
        db.add(
            EngineMetric(
                engine="wan",
                success_count=3,
                failure_count=2,
                total_render_time_sec=7.0,
                total_queue_latency_sec=3.0,
                qc_failure_count=1,
            )
        )
        db.commit()

    removed = dedupe_engine_metrics(dbmod.engine)
    assert removed >= 1
    with dbmod.SessionLocal() as db:
        rows = db.query(EngineMetric).filter(EngineMetric.engine == "wan").all()
        assert len(rows) == 1
        assert rows[0].success_count == 5
        assert rows[0].failure_count == 3
        assert float(rows[0].total_render_time_sec) == 12.0


def test_admin_repair_metrics_endpoint(client):
    import gateway.database as dbmod
    from sqlalchemy import text

    with dbmod.engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS uq_engine_metrics_engine"))

    with dbmod.SessionLocal() as db:
        db.add(
            EngineMetric(
                engine="framepack",
                success_count=0,
                failure_count=0,
                total_render_time_sec=0.0,
                total_queue_latency_sec=0.0,
                qc_failure_count=0,
            )
        )
        db.add(
            EngineMetric(
                engine="framepack",
                success_count=1,
                failure_count=0,
                total_render_time_sec=1.0,
                total_queue_latency_sec=0.0,
                qc_failure_count=0,
            )
        )
        db.commit()

    r = client.post(
        "/v1/admin/repair-metrics",
        headers={"X-API-Key": "test-api-key"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["duplicates_removed"] >= 1

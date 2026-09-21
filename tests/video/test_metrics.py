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

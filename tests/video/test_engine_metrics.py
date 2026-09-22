"""Regression coverage for first-use engine metrics and accumulated totals."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from gateway.database import Base
from gateway.metrics import record_engine_result
from gateway.models import EngineMetric

@pytest.mark.parametrize("success,qc_failed", [(True, False), (False, False), (False, True)])
def test_first_engine_result(success, qc_failed):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as db:
        record_engine_result(db, "ltx", success=success, qc_failed=qc_failed,
                             render_time_sec=12.5, queue_latency_sec=2.5)
        row = db.query(EngineMetric).one()
        assert row.success_count == int(success)
        assert row.failure_count == int(not success)
        assert row.qc_failure_count == int(qc_failed)
        assert row.total_render_time_sec == (12.5 if success else 0)
        assert row.total_queue_latency_sec == (2.5 if success else 0)
        record_engine_result(db, "ltx", success=True, render_time_sec=4, queue_latency_sec=1)
        db.expire_all()
        row = db.query(EngineMetric).one()
        assert row.success_count == int(success) + 1
        assert row.failure_count == int(not success)
        assert row.qc_failure_count == int(qc_failed)
        assert row.total_render_time_sec == (12.5 if success else 0) + 4
        assert row.total_queue_latency_sec == (2.5 if success else 0) + 1

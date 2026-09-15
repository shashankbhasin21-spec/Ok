"""Regression coverage for real-money reporting and fail-closed configuration."""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from earner.firm.payouts import PayoutError, PayoutStore
from earner.firm.review import pipeline_metrics
from earner.firm.store import FirmStore
from earner.firm.vertical_slice import integration_status


def test_receipts_exclude_simulation_foreign_currency_and_previous_month(tmp_path):
    store = FirmStore(tmp_path / "firm.db")
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    try:
        cases = [
            ("stripe", False, "usd", 100, start + 1),
            ("sandbox", False, "usd", 200, start + 1),
            ("stripe", True, "usd", 300, start + 1),
            ("stripe", False, "eur", 400, start + 1),
            ("stripe", False, "usd", 500, start - 1),
        ]
        for n, (provider, simulated, currency, amount, timestamp) in enumerate(cases):
            inv = store.record_firm_invoice(
                project_id="fixture",
                provider=provider,
                provider_ref=str(n),
                amount_cents=amount,
                currency=currency,
                simulated=simulated,
            )
            with patch("earner.firm.store.time.time", return_value=timestamp):
                assert store.confirm_payment(inv["id"], str(n), amount, currency)
                assert not store.confirm_payment(inv["id"], str(n), amount, currency)
        fin = pipeline_metrics(store)["finance_usd"]
        assert fin["gross_revenue_cents"] == 600
        assert fin["monthly_settled_cash_cents"] == 100
        assert fin["simulated_receipts_cents"] == 500
        assert fin["monthly_target_cents"] == 30_000_000
    finally:
        store.close()


def test_payout_missing_configuration_and_expired_session(tmp_path, monkeypatch):
    ps = PayoutStore(tmp_path / "payout.json")
    monkeypatch.delenv("FIRM_OWNER_SECRET", raising=False)
    with pytest.raises(PayoutError, match="must be configured"):
        ps.authenticate("owner-dev-secret")
    monkeypatch.setenv("FIRM_OWNER_SECRET", "private-test-password")
    token = ps.authenticate("private-test-password")
    monkeypatch.delenv("FIRM_PAYOUT_KEY", raising=False)
    with pytest.raises(PayoutError, match="encryption requires"):
        ps.save({}, token)
    assert not ps.path.exists()
    with patch("earner.firm.payouts.time.monotonic", return_value=ps._session_expires_at):
        with pytest.raises(PayoutError, match="reauthentication required"):
            ps.save({}, token)


def test_configuration_is_not_live_verification(monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "STRIPE_API_KEY", "UPWORK_CLIENT_ID", "GMAIL_USER"):
        monkeypatch.setenv(key, "configured-but-unverified")
    monkeypatch.setenv("EARNER_MODE", "live")
    status = integration_status()
    assert all(
        status[k] == "configured — unverified"
        for k in ("anthropic", "stripe", "upwork", "gmail")
    )

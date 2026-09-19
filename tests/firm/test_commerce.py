"""Grey Quantum commerce — loops, teams, CEO, checkout honesty."""

from __future__ import annotations

from pathlib import Path

import pytest

from earner.commerce.catalog import bootstrap_catalog, margin_cents
from earner.commerce.ceo import QuantumBrainCEO
from earner.commerce.engine import open_commerce, snapshot
from earner.commerce.orders import create_customer_order
from earner.commerce.store import CommerceStore


@pytest.fixture
def commerce(tmp_path: Path):
    store, ceo = open_commerce(tmp_path / "commerce")
    yield store, ceo
    store.close()


def test_margin_math():
    assert margin_cents(1000, 3000, 400) > 800


def test_bootstrap_and_ceo_day(commerce):
    store, ceo = commerce
    products = bootstrap_catalog(store)
    assert len(products) >= 4
    assert any(p["status"] == "compliance_hold" for p in products)

    result = ceo.run_company_day(publish=True, outreach=True)
    assert result["production_finder"]["loops_found"] >= 1
    assert result["loophole_squad"]["products_drafted"]
    assert result["daily_promotion"]["ad_drafts"] >= 3
    assert result["discussion_room"]["window_minutes"] == 120
    assert result["status"]["ceo"] == "Anestasis Grey"
    assert result["status"]["targets"]["daily_sales_usd"] == 10000
    assert result["status"]["targets"]["monthly_sales_usd"] == 500000

    published = store.list_products(published_only=True)
    assert published
    assert all(Path(p["page_html_path"]).exists() for p in published if p.get("page_html_path"))


def test_checkout_refuses_unpublished_and_placeholders(commerce):
    store, ceo = commerce
    ceo.run_company_day(publish=False)
    draft = store.list_products()[0]
    with pytest.raises(ValueError, match="not for sale"):
        create_customer_order(
            store,
            product_slug=draft["slug"],
            customer_email="buyer@gmail.com",
            ship_country="IN",
        )

    ceo.run_company_day(publish=True)
    live = store.list_products(published_only=True)[0]
    with pytest.raises(ValueError, match="placeholder"):
        create_customer_order(
            store,
            product_id=live["id"],
            customer_email="test@example.com",
            ship_country="IN",
        )

    order = create_customer_order(
        store,
        product_id=live["id"],
        customer_email="buyer@gmail.com",
        customer_name="Riya",
        ship_country="IN",
    )
    assert order["status"] == "pending_payment"
    assert store.settled_sales_cents() == 0

    with pytest.raises(ValueError, match="provider"):
        store.mark_order_paid(order["id"], provider="", provider_ref="")

    paid = store.mark_order_paid(
        order["id"], provider="stripe", provider_ref="pi_test_evidence"
    )
    assert paid["status"] == "paid"
    assert store.settled_sales_cents() == order["amount_cents"]


def test_snapshot_includes_teams(commerce):
    store, ceo = commerce
    ceo.run_company_day(publish=True)
    snap = snapshot(store, ceo)
    codes = {t["code"] for t in snap["teams"]}
    assert "F_quantum_ceo" in codes
    assert "D_loophole_squad" in codes
    assert snap["company"] == "Grey Quantum"

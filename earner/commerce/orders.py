"""Order intake — unpaid reservations until provider confirms payment."""

from __future__ import annotations

import re

from .store import CommerceStore

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PLACEHOLDER_DOMAINS = {"example.com", "test.com", "yourcompany.com"}


def create_customer_order(
    store: CommerceStore,
    *,
    product_id: str | None = None,
    product_slug: str | None = None,
    customer_email: str,
    customer_name: str = "",
    ship_country: str = "IN",
) -> dict:
    email = (customer_email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("valid customer_email required")
    domain = email.split("@", 1)[1]
    if domain in PLACEHOLDER_DOMAINS or email.startswith("test@"):
        raise ValueError("placeholder / test emails refused")
    if product_slug:
        product = store.get_product_by_slug(product_slug)
    elif product_id:
        product = store.get_product(product_id)
    else:
        raise ValueError("product_id or product_slug required")
    if product["status"] != "published":
        raise ValueError(f"product not for sale (status={product['status']})")
    if product.get("simulated"):
        raise ValueError("simulated products cannot take real orders")

    return store.create_order(
        product_id=product["id"],
        customer_email=email,
        customer_name=customer_name.strip() or None,
        ship_country=ship_country.upper()[:12],
        amount_cents=int(product["sell_price_cents"]),
        currency=product.get("currency") or "USD",
        status="pending_payment",
        simulated=False,
    )

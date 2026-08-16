"""Payment rails.

An agent never decides that it has been paid — it asks a provider, and the
provider's answer is the only thing the ledger will accept. Two providers ship
here: a sandbox that mimics the contract without moving money, and Stripe,
which moves real money.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

OPEN = "open"
PAID = "paid"
VOID = "void"


@dataclass
class InvoiceHandle:
    provider: str
    provider_ref: str
    amount_cents: int
    currency: str
    status: str
    url: str | None = None


@dataclass
class Settlement:
    """A provider-confirmed payment. ``event_id`` must be stable per payment."""

    provider_ref: str
    event_id: str
    amount_cents: int
    currency: str


class PaymentProvider(Protocol):
    name: str

    def create_invoice(
        self,
        *,
        customer_email: str,
        description: str,
        amount_cents: int,
        currency: str,
        metadata: dict | None = None,
    ) -> InvoiceHandle: ...

    def fetch_status(self, provider_ref: str) -> str: ...

    def fetch_settlement(self, provider_ref: str) -> Settlement | None:
        """Return a Settlement only when the provider says the money landed."""
        ...


class SandboxProvider:
    """Simulates the provider contract without touching real money.

    Invoices open and stay open. Nothing marks itself paid — a sandbox run
    reports zero revenue unless you deliberately call ``mark_paid``, which is
    what makes sandbox numbers safe to look at.
    """

    name = "sandbox"

    def __init__(self, state_path: Path | str | None = None):
        self.state_path = Path(state_path) if state_path else None
        self._invoices: dict[str, dict] = {}
        if self.state_path and self.state_path.exists():
            self._invoices = json.loads(self.state_path.read_text())

    def _save(self) -> None:
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self._invoices, indent=2))

    def create_invoice(
        self,
        *,
        customer_email: str,
        description: str,
        amount_cents: int,
        currency: str,
        metadata: dict | None = None,
    ) -> InvoiceHandle:
        ref = f"sbx_{uuid.uuid4().hex[:16]}"
        self._invoices[ref] = {
            "customer_email": customer_email,
            "description": description,
            "amount_cents": amount_cents,
            "currency": currency,
            "status": OPEN,
            "metadata": metadata or {},
            "paid_at": None,
        }
        self._save()
        return InvoiceHandle(
            provider=self.name,
            provider_ref=ref,
            amount_cents=amount_cents,
            currency=currency,
            status=OPEN,
            url=f"sandbox://invoice/{ref}",
        )

    def fetch_status(self, provider_ref: str) -> str:
        return self._invoices[provider_ref]["status"]

    def fetch_settlement(self, provider_ref: str) -> Settlement | None:
        inv = self._invoices.get(provider_ref)
        if not inv or inv["status"] != PAID:
            return None
        return Settlement(
            provider_ref=provider_ref,
            event_id=f"{provider_ref}:{inv['paid_at']}",
            amount_cents=inv["amount_cents"],
            currency=inv["currency"],
        )

    def mark_paid(self, provider_ref: str) -> None:
        """Test/demo hook. Simulated payments are clearly labelled as such."""
        inv = self._invoices[provider_ref]
        if inv["status"] != PAID:
            inv["status"] = PAID
            inv["paid_at"] = int(time.time())
            self._save()


class StripeProvider:
    """Real invoices, real money.

    Uses the Invoice API rather than a bare payment link so the customer gets
    a document they can pay, forward, and file — and so ``fetch_settlement``
    has an authoritative status to read back.
    """

    name = "stripe"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("StripeProvider requires an API key")
        try:
            import stripe  # imported lazily so sandbox runs need no dependency
        except ImportError as exc:  # pragma: no cover - depends on install extras
            raise RuntimeError("pip install 'earner[stripe]' to use the Stripe provider") from exc
        stripe.api_key = api_key
        self._stripe = stripe

    def create_invoice(
        self,
        *,
        customer_email: str,
        description: str,
        amount_cents: int,
        currency: str,
        metadata: dict | None = None,
    ) -> InvoiceHandle:
        s = self._stripe
        customers = s.Customer.list(email=customer_email, limit=1).data
        customer = customers[0] if customers else s.Customer.create(email=customer_email)
        invoice = s.Invoice.create(
            customer=customer.id,
            collection_method="send_invoice",
            days_until_due=7,
            metadata={k: str(v) for k, v in (metadata or {}).items()},
            auto_advance=False,
        )
        s.InvoiceItem.create(
            customer=customer.id,
            invoice=invoice.id,
            amount=amount_cents,
            currency=currency,
            description=description[:500],
        )
        invoice = s.Invoice.finalize_invoice(invoice.id)
        s.Invoice.send_invoice(invoice.id)
        return InvoiceHandle(
            provider=self.name,
            provider_ref=invoice.id,
            amount_cents=amount_cents,
            currency=currency,
            status=invoice.status,
            url=getattr(invoice, "hosted_invoice_url", None),
        )

    def fetch_status(self, provider_ref: str) -> str:
        return self._stripe.Invoice.retrieve(provider_ref).status

    def fetch_settlement(self, provider_ref: str) -> Settlement | None:
        invoice = self._stripe.Invoice.retrieve(provider_ref)
        if invoice.status != PAID:
            return None
        paid_at = getattr(invoice.status_transitions, "paid_at", None) or "paid"
        return Settlement(
            provider_ref=provider_ref,
            # Stable per payment: the same invoice paid once yields one event id,
            # so re-polling or replaying a webhook cannot double-count.
            event_id=f"{invoice.id}:{paid_at}",
            amount_cents=invoice.amount_paid,
            currency=invoice.currency,
        )


def build_provider(cfg) -> PaymentProvider:
    """Pick a provider from config. Live mode is opt-in and key-gated."""
    if cfg.is_live:
        return StripeProvider(cfg.stripe_api_key or "")
    return SandboxProvider(cfg.workdir / "sandbox_invoices.json")

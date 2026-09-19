"""Commerce engine — wire store + CEO for API and CLI."""

from __future__ import annotations

from pathlib import Path

from .ceo import QuantumBrainCEO
from .store import CommerceStore


def open_commerce(workdir: Path) -> tuple[CommerceStore, QuantumBrainCEO]:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    store = CommerceStore(workdir / "commerce.db")
    ceo = QuantumBrainCEO(store, workdir)
    return store, ceo


def snapshot(store: CommerceStore, ceo: QuantumBrainCEO) -> dict:
    status = ceo.status()
    return {
        **status,
        "products": store.list_products(include_simulated=False),
        "published_products": store.list_products(published_only=True),
        "loops": store.list_loops(30),
        "ads": store.list_ad_drafts(30),
        "orders": store.list_orders(),
        "ideas": store.list_ideas(20),
        "outreach_drafts": store.list_outreach_drafts(20),
        "directives": store.list_directives(20),
    }

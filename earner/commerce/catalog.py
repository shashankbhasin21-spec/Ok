"""Seed niches and product scaffolding from public research hypotheses.

These are starting catalogs with documented price hypotheses — not live
marketplace scrapes and not verified inventory. Production finder agents promote
items only after margin math clears shipping + fees.
"""

from __future__ import annotations

from .store import CommerceStore

# Public niche hypotheses for India + world demand. Prices are research seeds
# in USD cents; live quoting must re-verify before publishing.
SEED_NICHES: list[dict] = [
    {
        "slug": "compact-desk-lamp-in",
        "title": "Compact LED Desk Lamp — Matte Black",
        "niche": "home_office",
        "description": (
            "Adjustable LED desk lamp for small workspaces. Ships India-first with "
            "global option. Source cost hypothesis from wholesale B2B lists; retail "
            "positioned under premium Amazon peers."
        ),
        "source_market": "CN_wholesale",
        "source_url": "https://www.alibaba.com/",
        "source_price_cents": 1200,
        "sell_price_cents": 3499,
        "shipping_estimate_cents": 600,
        "evidence_note": (
            "Seed hypothesis only — verify supplier MOQ, HS code, and landed cost "
            "before publishing or advertising."
        ),
    },
    {
        "slug": "stainless-lunch-box-in",
        "title": "Stainless Steel Lunch Box Set",
        "niche": "kitchen",
        "description": (
            "Leak-resistant stainless lunch set for office workers in India metros. "
            "High repeat purchase niche; margin after domestic courier."
        ),
        "source_market": "IN_wholesale",
        "source_url": "https://www.indiamart.com/",
        "source_price_cents": 900,
        "sell_price_cents": 2499,
        "shipping_estimate_cents": 350,
        "evidence_note": "Seed hypothesis — confirm GST invoice path and return rate.",
    },
    {
        "slug": "resistance-band-kit-global",
        "title": "Resistance Band Kit (5 levels)",
        "niche": "fitness",
        "description": (
            "Portable resistance kit for home fitness. Global demand, light shipping "
            "weight supports India→world and China→IN loops."
        ),
        "source_market": "CN_wholesale",
        "source_url": "https://www.alibaba.com/",
        "source_price_cents": 700,
        "sell_price_cents": 2799,
        "shipping_estimate_cents": 450,
        "evidence_note": "Seed hypothesis — check stretch durability claims; no medical claims.",
    },
    {
        "slug": "cable-organizer-pro",
        "title": "Magnetic Cable Organizer Pack",
        "niche": "gadgets",
        "description": (
            "Desk cable clips for hybrid workers. Low weight, high perceived value, "
            "strong Meta/Reels creative fit."
        ),
        "source_market": "GLOBAL",
        "source_url": "https://www.alibaba.com/",
        "source_price_cents": 350,
        "sell_price_cents": 1499,
        "shipping_estimate_cents": 250,
        "evidence_note": "Seed hypothesis — confirm magnetic strength photos are accurate.",
    },
    {
        "slug": "ayurveda-tea-sampler",
        "title": "Ayurveda Evening Tea Sampler",
        "niche": "wellness_india",
        "description": (
            "India-origin herbal tea sampler for domestic and diaspora buyers. "
            "Food compliance and FSSAI labeling required before live sales."
        ),
        "source_market": "IN_wholesale",
        "source_url": "https://www.indiamart.com/",
        "source_price_cents": 500,
        "sell_price_cents": 1899,
        "shipping_estimate_cents": 300,
        "evidence_note": "BLOCKED for live food sale until FSSAI/label evidence attached.",
    },
]


def margin_cents(source: int, sell: int, shipping: int, fee_bps: int = 350) -> int:
    """Gross margin after shipping and an illustrative payment fee (bps of sell)."""
    fee = (sell * fee_bps) // 10_000
    return sell - source - shipping - fee


def bootstrap_catalog(store: CommerceStore) -> list[dict]:
    """Load seed products if catalog empty. Marks them draft, not published."""
    existing = store.list_products(include_simulated=True)
    if existing:
        return existing
    out = []
    for seed in SEED_NICHES:
        m = margin_cents(
            seed["source_price_cents"],
            seed["sell_price_cents"],
            seed["shipping_estimate_cents"],
        )
        status = "draft"
        # Food items stay blocked until compliance evidence exists.
        if seed["niche"] == "wellness_india":
            status = "compliance_hold"
        out.append(
            store.upsert_product(
                slug=seed["slug"],
                title=seed["title"],
                niche=seed["niche"],
                description=seed["description"],
                source_market=seed["source_market"],
                source_url=seed["source_url"],
                source_price_cents=seed["source_price_cents"],
                sell_price_cents=seed["sell_price_cents"],
                shipping_estimate_cents=seed["shipping_estimate_cents"],
                margin_cents=m,
                status=status,
                evidence_note=seed["evidence_note"],
                simulated=False,
            )
        )
    return out

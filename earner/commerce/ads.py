"""Ad draft generators for Meta, YouTube, Instagram — drafts only.

Publishing and spend require owner approval and live ad-account credentials.
Never claim performance metrics that were not measured.
"""

from __future__ import annotations

from .store import CommerceStore

CHANNELS = ("meta", "youtube", "instagram")


def draft_ads_for_product(store: CommerceStore, product: dict) -> list[dict]:
    title = product["title"]
    price = product["sell_price_cents"] / 100
    niche = product["niche"].replace("_", " ")
    drafts = []

    specs = {
        "meta": {
            "headline": f"{title} — ships India & world",
            "body": (
                f"Grey Quantum lists verified-margin {niche} gear. "
                f"Priced at ${price:.2f}. No fake scarcity. Order on greyquantum.store "
                f"when checkout is live."
            ),
            "cta": "Shop now",
        },
        "youtube": {
            "headline": f"Unbox: {title}",
            "body": (
                f"30s product demo. Show real unit, packing, and shipping regions "
                f"(India + international). CTA: Grey Quantum product page. "
                f"Do not invent reviews."
            ),
            "cta": "Watch & buy",
        },
        "instagram": {
            "headline": f"{title}",
            "body": (
                f"Reel hook: one clear use-case in 3 seconds. Caption names price "
                f"(${price:.2f}) and ships-to markets. Soft CTA to store page. "
                f"No bought engagement."
            ),
            "cta": "Link in bio",
        },
    }

    for channel, spec in specs.items():
        drafts.append(
            store.add_ad_draft(
                product_id=product["id"],
                channel=channel,
                headline=spec["headline"],
                body=spec["body"],
                cta=spec["cta"],
                status="draft",
                spend_cap_cents=0,  # spend stays 0 until owner sets a cap
            )
        )
    return drafts


def draft_outreach(store: CommerceStore, *, channel: str, audience: str, product_title: str) -> dict:
    """Cold DM / message drafts — never auto-send."""
    templates = {
        "instagram_dm": (
            f"Hi — Anestasis Grey's team at Grey Quantum just listed {product_title}. "
            f"If you want the product page link (no pressure), reply YES and we'll share it. "
            f"Opt out anytime."
        ),
        "email": (
            f"Subject: {product_title} now on Grey Quantum\n\n"
            f"Quick note from Grey Quantum (CEO Anestasis Grey). We opened a listing for "
            f"{product_title} with transparent pricing for India and international shipping. "
            f"Reply if you'd like the page — we won't spam."
        ),
        "cold_call_script": (
            f"Intro: Calling from Grey Quantum under Anestasis Grey. "
            f"We source {product_title} with a verified margin loop and sell direct. "
            f"Ask permission for 30 seconds. If no, thank and end. Never pressure."
        ),
    }
    if channel not in templates:
        raise ValueError(f"unsupported outreach channel: {channel}")
    return store.add_outreach_draft(
        channel=channel,
        audience=audience,
        message=templates[channel],
        status="draft",
    )

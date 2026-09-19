"""Agent teams for Grey Quantum under CEO Anestasis Grey."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import CEO_NAME, DISCUSSION_WINDOW_MINUTES
from .ads import draft_ads_for_product, draft_outreach
from .arbitrage import persist_loops, scan_daily_loopholes, scan_seed_loops
from .catalog import bootstrap_catalog, margin_cents
from .pages import write_product_page
from .store import CommerceStore

TEAM_DEFS: list[dict[str, Any]] = [
    {
        "code": "A_production_finder",
        "name": "Production Finder",
        "mission": "Scan niches for buy→ship→sell loops with positive margin after fees.",
        "headcount": 1,
    },
    {
        "code": "B_webpage_developer",
        "name": "Custom Web Page Developer",
        "mission": "Turn approved products into published store pages.",
        "headcount": 1,
    },
    {
        "code": "C_daily_promotion",
        "name": "Daily Promotion & Ads",
        "mission": "Draft Meta, YouTube, and Instagram ads; never spend without owner cap.",
        "headcount": 1,
    },
    {
        "code": "D_loophole_squad",
        "name": "Loophole Squad (5)",
        "mission": "Find daily niche loopholes and queue implementations.",
        "headcount": 5,
    },
    {
        "code": "E_discussion_room",
        "name": "Daily Discussion Room",
        "mission": f"Two-hour ({DISCUSSION_WINDOW_MINUTES}m) idea room for new product angles.",
        "headcount": 1,
    },
    {
        "code": "F_quantum_ceo",
        "name": f"Quantum Brain CEO — {CEO_NAME}",
        "mission": "Full command: prioritize loops, approve publishes, drive toward targets.",
        "headcount": 1,
    },
    {
        "code": "G_outreach_optional",
        "name": "Outreach Specialists (optional)",
        "mission": "Draft cold-call scripts and marketing DMs; send only with owner approval.",
        "headcount": 2,
    },
]


def ensure_teams(store: CommerceStore) -> list[dict]:
    return [store.upsert_team(**t) for t in TEAM_DEFS]


def _mark_team(store: CommerceStore, code: str, output: dict) -> None:
    store.upsert_team(
        **next(t for t in TEAM_DEFS if t["code"] == code),
        status="ran",
        last_run_at=time.time(),
        last_output_json=json.dumps(output),
    )


def run_production_finder(store: CommerceStore) -> dict:
    bootstrap_catalog(store)
    loops = persist_loops(store, scan_seed_loops())
    out = {"loops_found": len(loops), "loop_ids": [x["id"] for x in loops]}
    _mark_team(store, "A_production_finder", out)
    return out


def run_loophole_squad(store: CommerceStore, workdir: Path) -> dict:
    candidates = scan_daily_loopholes()
    loops = persist_loops(store, candidates)
    implemented = []
    for loop, cand in zip(loops, candidates):
        # Implement as draft product page-ready SKU
        slug = f"{cand.niche}-{loop['id'][-6:]}"
        shipping = max(200, int(cand.source_price_cents * 0.4))
        m = margin_cents(cand.source_price_cents, cand.sell_price_cents, shipping)
        title = cand.hypothesis.split(":")[0].strip()[:80]
        product = store.upsert_product(
            slug=slug,
            title=title,
            niche=cand.niche,
            description=cand.hypothesis,
            source_market="GLOBAL",
            source_price_cents=cand.source_price_cents,
            sell_price_cents=cand.sell_price_cents,
            shipping_estimate_cents=shipping,
            margin_cents=m,
            status="draft",
            evidence_note=cand.risk_note,
        )
        path = write_product_page(workdir, product)
        product = store.upsert_product(
            slug=product["slug"],
            title=product["title"],
            niche=product["niche"],
            description=product["description"],
            source_market=product["source_market"],
            source_price_cents=product["source_price_cents"],
            sell_price_cents=product["sell_price_cents"],
            shipping_estimate_cents=product["shipping_estimate_cents"],
            margin_cents=product["margin_cents"],
            status="draft",
            page_html_path=str(path),
            evidence_note=product["evidence_note"],
        )
        store.attach_loop_product(loop["id"], product["id"])
        implemented.append(product["id"])
    out = {"loops": len(loops), "products_drafted": implemented}
    _mark_team(store, "D_loophole_squad", out)
    return out


def run_webpage_developer(store: CommerceStore, workdir: Path, *, publish: bool = False) -> dict:
    pages = []
    for product in store.list_products(include_simulated=True):
        if product["status"] == "compliance_hold":
            continue
        path = write_product_page(workdir, product)
        new_status = "published" if publish and product["status"] in ("draft", "published") else product["status"]
        if publish and product["status"] == "draft":
            new_status = "published"
        store.upsert_product(
            slug=product["slug"],
            title=product["title"],
            niche=product["niche"],
            description=product["description"],
            source_market=product["source_market"],
            source_url=product.get("source_url"),
            source_price_cents=product["source_price_cents"],
            sell_price_cents=product["sell_price_cents"],
            shipping_estimate_cents=product["shipping_estimate_cents"],
            margin_cents=product["margin_cents"],
            status=new_status,
            page_html_path=str(path),
            evidence_note=product["evidence_note"],
        )
        pages.append({"slug": product["slug"], "path": str(path), "status": new_status})
    out = {"pages": pages, "published": publish}
    _mark_team(store, "B_webpage_developer", out)
    return out


def run_daily_promotion(store: CommerceStore) -> dict:
    ads = []
    for product in store.list_products(published_only=True):
        ads.extend(draft_ads_for_product(store, product))
    # If nothing published yet, draft against drafts so creatives are ready
    if not ads:
        for product in store.list_products()[:3]:
            ads.extend(draft_ads_for_product(store, product))
    out = {"ad_drafts": len(ads), "channels": ["meta", "youtube", "instagram"]}
    _mark_team(store, "C_daily_promotion", out)
    return out


def run_discussion_room(store: CommerceStore) -> dict:
    ideas = [
        store.add_idea(
            title="Bundle desk lamp + cable organizer",
            body="Increase AOV for home_office niche; single shipping weight class.",
            niche="home_office",
            raised_by="discussion_room",
        ),
        store.add_idea(
            title="India UPI express checkout",
            body="Add Razorpay/UPI when owner supplies merchant credentials. Kotak payout later.",
            niche="payments",
            raised_by="discussion_room",
        ),
        store.add_idea(
            title="YouTube Shorts demo factory",
            body="One 20s demo per published SKU weekly; pause if ad account missing.",
            niche="content",
            raised_by="discussion_room",
        ),
    ]
    out = {
        "window_minutes": DISCUSSION_WINDOW_MINUTES,
        "ideas": [i["id"] for i in ideas],
        "note": "Ideas logged — execution still needs CEO directive + owner auth for spend.",
    }
    _mark_team(store, "E_discussion_room", out)
    return out


def run_outreach_optional(store: CommerceStore) -> dict:
    products = store.list_products(published_only=True) or store.list_products()[:1]
    drafts = []
    if products:
        title = products[0]["title"]
        for channel in ("instagram_dm", "email", "cold_call_script"):
            drafts.append(
                draft_outreach(
                    store,
                    channel=channel,
                    audience="warm_interest_list",
                    product_title=title,
                )
            )
    out = {"outreach_drafts": len(drafts), "status": "draft_only"}
    _mark_team(store, "G_outreach_optional", out)
    return out

"""Arbitrage loop scanner — finds candidate buy→ship→sell spreads.

Uses seeded public niche research plus optional owner-supplied scans.
Does not scrape private accounts or bypass marketplace terms. Loops are
candidates until a product page is published and inventory is verified.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import SEED_NICHES, margin_cents
from .store import CommerceStore

MIN_MARGIN_CENTS = 800  # refuse loops below ~$8 gross after fees/shipping


@dataclass
class LoopCandidate:
    niche: str
    hypothesis: str
    source_price_cents: int
    sell_price_cents: int
    estimated_margin_cents: int
    risk_note: str
    found_by: str


# Extra daily loophole hypotheses the 5-agent team rotates through.
DAILY_LOOP_HYPOTHESES: list[dict] = [
    {
        "niche": "pet_accessories",
        "title_hint": "Collapsible Pet Travel Bowl",
        "source_price_cents": 400,
        "sell_price_cents": 1699,
        "shipping_estimate_cents": 280,
        "hypothesis": "Light SKU; Instagram Reels demo converts well; India + US demand.",
        "risk_note": "Verify food-grade plastic claims; returns on lids.",
        "agent": "loophole_scout_1",
    },
    {
        "niche": "travel",
        "title_hint": "Packing Cube Set (3)",
        "source_price_cents": 1100,
        "sell_price_cents": 3299,
        "shipping_estimate_cents": 500,
        "hypothesis": "Seasonal travel spikes; cross-list IN domestic + EU diaspora.",
        "risk_note": "Zipper durability; color variance photos must match stock.",
        "agent": "loophole_scout_2",
    },
    {
        "niche": "beauty_tools",
        "title_hint": "Jade Facial Roller",
        "source_price_cents": 550,
        "sell_price_cents": 2199,
        "shipping_estimate_cents": 320,
        "hypothesis": "High creative volume on Meta; low weight for India→world.",
        "risk_note": "No medical claims; stone authenticity disclosure.",
        "agent": "loophole_scout_3",
    },
    {
        "niche": "baby_gear",
        "title_hint": "Silicone Bib Duo",
        "source_price_cents": 650,
        "sell_price_cents": 2499,
        "shipping_estimate_cents": 300,
        "hypothesis": "Repeat gift purchases; strong India urban demand.",
        "risk_note": "Child-product safety standards before live publish.",
        "agent": "loophole_scout_4",
    },
    {
        "niche": "kitchen",
        "title_hint": "Herb Scissor Set",
        "source_price_cents": 480,
        "sell_price_cents": 1899,
        "shipping_estimate_cents": 280,
        "hypothesis": "Demo video converts; complements lunch-box niche.",
        "risk_note": "Blade shipping rules for some carriers.",
        "agent": "loophole_scout_5",
    },
]


def scan_seed_loops() -> list[LoopCandidate]:
    out: list[LoopCandidate] = []
    for seed in SEED_NICHES:
        m = margin_cents(
            seed["source_price_cents"],
            seed["sell_price_cents"],
            seed["shipping_estimate_cents"],
        )
        if m < MIN_MARGIN_CENTS:
            continue
        out.append(
            LoopCandidate(
                niche=seed["niche"],
                hypothesis=(
                    f"Buy {seed['title']} near ${seed['source_price_cents']/100:.2f}, "
                    f"ship (~${seed['shipping_estimate_cents']/100:.2f}), "
                    f"sell at ${seed['sell_price_cents']/100:.2f}."
                ),
                source_price_cents=seed["source_price_cents"],
                sell_price_cents=seed["sell_price_cents"],
                estimated_margin_cents=m,
                risk_note=seed["evidence_note"],
                found_by="production_finder",
            )
        )
    return out


def scan_daily_loopholes() -> list[LoopCandidate]:
    out: list[LoopCandidate] = []
    for h in DAILY_LOOP_HYPOTHESES:
        m = margin_cents(
            h["source_price_cents"],
            h["sell_price_cents"],
            h["shipping_estimate_cents"],
        )
        if m < MIN_MARGIN_CENTS:
            continue
        out.append(
            LoopCandidate(
                niche=h["niche"],
                hypothesis=f"{h['title_hint']}: {h['hypothesis']}",
                source_price_cents=h["source_price_cents"],
                sell_price_cents=h["sell_price_cents"],
                estimated_margin_cents=m,
                risk_note=h["risk_note"],
                found_by=h["agent"],
            )
        )
    return out


def persist_loops(store: CommerceStore, candidates: list[LoopCandidate]) -> list[dict]:
    saved = []
    for c in candidates:
        saved.append(
            store.add_loop(
                niche=c.niche,
                hypothesis=c.hypothesis,
                source_price_cents=c.source_price_cents,
                sell_price_cents=c.sell_price_cents,
                estimated_margin_cents=c.estimated_margin_cents,
                risk_note=c.risk_note,
                status="candidate",
                found_by=c.found_by,
            )
        )
    return saved

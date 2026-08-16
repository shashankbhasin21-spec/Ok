"""Bidding — scans job boards, skips the losers, drafts bids worth sending.

Two deliberate limits, both of which make it earn more rather than less:

It **drafts**, it does not submit. Freelancer and Upwork do not offer public
bid-submission APIs and their terms prohibit automated bidding; an account ban
costs more than any bid wins. You paste the draft yourself.

It **refuses most postings**. The scoring is plain code, not a model judgement,
because the two facts that decide whether a bid is worth writing — how many
people already bid, and what currency the budget is in — are arithmetic. A
200-bid contest at ₹23,278 is a lottery ticket that costs an hour to buy.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..agent import BaseAgent, TickReport
from ..approval import Action

SCAN_SCHEMA = {
    "type": "object",
    "properties": {
        "postings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "budget_low": {"type": "number"},
                    "budget_high": {"type": "number"},
                    "currency": {"type": "string"},
                    "bid_count": {"type": "integer"},
                    "requirements": {"type": "string"},
                    "acceptance_criteria": {"type": "string"},
                    "closes_in": {"type": "string"},
                },
                "required": [
                    "title", "url", "budget_low", "budget_high", "currency",
                    "bid_count", "requirements", "acceptance_criteria", "closes_in",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["postings"],
    "additionalProperties": False,
}


@dataclass
class Verdict:
    worth_bidding: bool
    reason: str
    bid_amount: float = 0.0
    currency: str = "usd"


class BiddingAgent(BaseAgent):
    name = "bidding"
    role = "Bidding"
    description = "Scans boards, filters out the unwinnable, drafts bids worth sending."

    system_prompt = (
        "You write bids for automation projects on freelance boards. You read the posting's "
        "acceptance criteria first and answer them directly, because that is what separates a "
        "bid from the thirty others that promise speed. You never claim clients, results or "
        "credentials that do not exist — one unverifiable claim loses the bid. You price near "
        "the top of the client's stated range when the criteria show they care about quality, "
        "because on these boards the lowest bid signals the posting went unread."
    )

    # A bid is worth an hour of your time only if you can plausibly win it.
    MAX_BID_COUNT = 45          # beyond this, per-bid win rate falls under ~2%
    MIN_BUDGET_USD = 150.0      # below this, the bid costs more to write than it pays
    # Rough rates; only used to compare postings on a common scale.
    USD_PER = {"usd": 1.0, "eur": 1.08, "gbp": 1.27, "aud": 0.66, "cad": 0.73, "inr": 1 / 96.2}

    def boards(self) -> list[str]:
        """Board URLs to scan, from ``inbox/boards.json``."""
        for _, data in self.read_inbox("boards"):
            urls = data.get("urls") or ([data["url"]] if data.get("url") else [])
            for url in urls:
                yield url

    def tick(self) -> TickReport:
        report = TickReport(agent=self.name)
        for board in self.boards():
            try:
                postings = self._scan(board)
            except Exception as exc:  # noqa: BLE001 - a bad board must not stop the rest
                report.errors.append(f"board {board}: {type(exc).__name__}: {exc}")
                continue

            for posting in postings:
                ref = "bid-" + hashlib.sha256(posting["url"].encode()).hexdigest()[:12]
                if self.ledger.record_opportunity(self.name, board, ref, posting) is None:
                    continue
                report.found += 1
                try:
                    self._consider(ref, posting, report)
                except Exception as exc:  # noqa: BLE001
                    report.errors.append(f"{posting['title'][:40]}: {type(exc).__name__}: {exc}")
        return report

    def _scan(self, board_url: str) -> list[dict]:
        raw = self.think(
            None,
            f"Read this job board and list every open posting: {board_url}\n\n"
            "For each, report the exact title, full URL, budget range with its currency, the "
            "number of bids already placed, what the client wants built, and any stated "
            "acceptance criteria.\n\n"
            "Report the currency the posting is actually denominated in — if the budget is "
            "shown in rupees, say INR, not USD. Leave numbers you cannot find as 0 rather than "
            "estimating them; a guessed bid count leads to bidding on a lottery.",
            schema=SCAN_SCHEMA,
            research=True,
            effort="medium",
            max_tokens=8000,
        )
        return json.loads(raw)["postings"]

    def score(self, posting: dict) -> Verdict:
        """Decide whether this is winnable and worth the hour. Pure arithmetic."""
        bids = int(posting.get("bid_count") or 0)
        currency = (posting.get("currency") or "usd").lower()
        high = float(posting.get("budget_high") or 0)
        low = float(posting.get("budget_low") or 0)

        rate = self.USD_PER.get(currency, 1.0)
        high_usd = high * rate

        if bids > self.MAX_BID_COUNT:
            return Verdict(
                False,
                f"{bids} bids already — per-bid win rate is roughly "
                f"{100 / max(bids, 1):.1f}%, which is a lottery ticket, not a pipeline",
            )
        if high_usd and high_usd < self.MIN_BUDGET_USD:
            return Verdict(
                False,
                f"top of range is ${high_usd:,.0f} — below the floor where a bid pays for "
                "the time it takes to write",
            )
        if not high:
            return Verdict(False, "no budget stated — cannot price it without guessing")

        # Bid near the top of their range: on these boards the low bid signals
        # the acceptance criteria went unread.
        amount = round(low + (high - low) * 0.85, -2) if high > low else high
        return Verdict(
            True,
            f"{bids} bids, ~{100 / max(bids, 1):.1f}% per-bid win rate, "
            f"${high_usd:,.0f} top of range",
            bid_amount=amount,
            currency=currency,
        )

    def _consider(self, ref: str, posting: dict, report: TickReport) -> None:
        verdict = self.score(posting)
        self.ledger.log(
            "opportunity", None, "bid_scored",
            ref=ref, title=posting["title"], worth=verdict.worth_bidding, reason=verdict.reason,
        )
        if not verdict.worth_bidding:
            report.rejected += 1
            return

        cheap_market = verdict.currency not in ("usd", "gbp", "eur", "aud", "cad")
        bid = self.think(
            None,
            "Write the bid for this posting.\n\n"
            f"Title: {posting['title']}\n"
            f"What they want: {posting['requirements']}\n"
            f"Acceptance criteria: {posting['acceptance_criteria']}\n"
            f"Budget: {verdict.currency.upper()} {posting['budget_low']}–{posting['budget_high']}\n"
            f"Bids so far: {posting['bid_count']}\n"
            f"We sell: {self.cfg.offer}\n\n"
            f"Bid {verdict.currency.upper()} {verdict.bid_amount:,.0f}.\n\n"
            "Open by answering their acceptance criteria — that is the client telling you what "
            "they were burned by last time. Give a day-by-day plan, not adjectives. End with one "
            "question whose answer you actually need to scope the work. No portfolio, no "
            "credentials paragraph, no invented past clients.",
            effort="medium",
            max_tokens=2500,
        )

        decision = self.gate.review(
            Action(
                kind="message",
                summary=f"bid on {posting['title'][:50]} "
                f"({posting['bid_count']} bids, {verdict.currency.upper()} {verdict.bid_amount:,.0f})",
                body=bid,
                recipient=posting["url"],
            )
        )
        if not decision.ok:
            report.held += 1
            return

        note = (
            f"# Bid: {posting['title']}\n\n"
            f"**Submit at:** {posting['url']}\n"
            f"**Amount:** {verdict.currency.upper()} {verdict.bid_amount:,.0f}\n"
            f"**Competition:** {posting['bid_count']} bids · closes {posting['closes_in']}\n"
            f"**Why worth it:** {verdict.reason}\n"
        )
        if cheap_market:
            note += (
                f"\n> This posting is denominated in {verdict.currency.upper()}. Treat it as "
                "portfolio-building, not as the business — see docs/where-to-sell.md.\n"
            )
        note += f"\n---\n\n{bid}\n"

        self.write_outbox(f"bid-{ref}.md", note)
        report.quoted += 1

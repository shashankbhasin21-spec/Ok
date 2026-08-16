"""The bidder's value is what it refuses to bid on."""

from __future__ import annotations

import pytest

from earner import approval
from earner.agents.bidding import BiddingAgent

from conftest import FakeLLM


def _bidder(cfg, ledger, provider, responses=None):
    return BiddingAgent(cfg, ledger, provider, approval.AutoGate(), FakeLLM(responses or []))


def _posting(**over):
    base = {
        "title": "AI automation for web platform", "url": "https://board.example/p/1",
        "budget_low": 1000.0, "budget_high": 3000.0, "currency": "usd",
        "bid_count": 12, "requirements": "build an agent", "acceptance_criteria": "tests",
        "closes_in": "3 days",
    }
    return {**base, **over}


def test_contested_postings_are_refused(cfg, ledger, provider):
    """202 bids is a lottery ticket, not a pipeline."""
    verdict = _bidder(cfg, ledger, provider).score(_posting(bid_count=202))
    assert verdict.worth_bidding is False
    assert "lottery" in verdict.reason


def test_a_winnable_posting_is_taken(cfg, ledger, provider):
    verdict = _bidder(cfg, ledger, provider).score(_posting(bid_count=12))
    assert verdict.worth_bidding is True


def test_it_bids_near_the_top_of_the_range(cfg, ledger, provider):
    """The low bid signals the acceptance criteria went unread."""
    verdict = _bidder(cfg, ledger, provider).score(_posting(budget_low=1000, budget_high=3000))
    assert verdict.bid_amount > 2000, "bidding at the bottom of the range loses on these boards"
    assert verdict.bid_amount <= 3000


def test_rupee_budgets_are_converted_before_comparing(cfg, ledger, provider):
    """A Rs 37,500 ceiling is ~$390 — above the floor, so still worth a bid."""
    verdict = _bidder(cfg, ledger, provider).score(
        _posting(budget_low=12500, budget_high=37500, currency="inr", bid_count=37)
    )
    assert verdict.worth_bidding is True
    assert verdict.currency == "inr"


def test_tiny_budgets_are_refused(cfg, ledger, provider):
    verdict = _bidder(cfg, ledger, provider).score(
        _posting(budget_low=1000, budget_high=5000, currency="inr")  # ~$52
    )
    assert verdict.worth_bidding is False
    assert "floor" in verdict.reason


def test_a_posting_with_no_budget_is_refused(cfg, ledger, provider):
    verdict = _bidder(cfg, ledger, provider).score(_posting(budget_low=0, budget_high=0))
    assert verdict.worth_bidding is False
    assert "without guessing" in verdict.reason


def test_the_firm_now_has_seven_staff(cfg):
    from earner.platform import Platform

    platform = Platform(cfg, gate_name="hold")
    try:
        assert "bidding" in platform.staff
        assert len(platform.staff) == 7
    finally:
        platform.close()

"""The Upwork channel: honest about what the API does and does not allow."""

from __future__ import annotations

import json

import pytest

from earner.channels.upwork import (
    AUTHORIZE_URL, GQL_ENDPOINT, TOKEN_URL, NotSupported, UpworkAuthError, UpworkChannel,
)


def _cfg(cfg, **over):
    for k, v in over.items():
        object.__setattr__(cfg, k, v)
    return cfg


def test_submitting_a_proposal_is_refused_with_the_reason(cfg):
    """Upwork exposes no submission mutation; pretending otherwise risks the account."""
    with pytest.raises(NotSupported, match="no mutation"):
        UpworkChannel(cfg).submit_proposal(job_id="x", text="y")


def test_official_endpoints_are_used(cfg):
    assert GQL_ENDPOINT == "https://api.upwork.com/graphql"
    assert TOKEN_URL == "https://www.upwork.com/api/v3/oauth2/token"
    assert AUTHORIZE_URL == "https://www.upwork.com/ab/account-security/oauth2/authorize"


def test_authorize_url_carries_the_grant_parameters(cfg):
    channel = UpworkChannel(_cfg(cfg, upwork_client_id="cid", upwork_client_secret="sec"))
    url = channel.authorize_url()
    assert url.startswith(AUTHORIZE_URL)
    assert "response_type=code" in url and "client_id=cid" in url


def test_unconfigured_channel_refuses_rather_than_half_working(cfg):
    channel = UpworkChannel(cfg)
    assert channel.configured is False and channel.enabled is False
    with pytest.raises(UpworkAuthError, match="UPWORK_CLIENT_ID"):
        channel.authorize_url()
    with pytest.raises(UpworkAuthError, match="Not authorised"):
        channel.access_token()


def test_fixed_price_and_hourly_postings_both_normalise(cfg):
    channel = UpworkChannel(cfg)

    fixed = channel._to_job({
        "id": "1", "title": "Build an agent", "description": "d", "ciphertext": "~01abc",
        "amount": {"rawValue": "1500", "currency": "USD"}, "totalApplicants": 8,
        "client": {"location": {"country": "United States"}, "totalSpent": {"rawValue": "50000"}},
    })
    assert (fixed.budget_low, fixed.budget_high, fixed.currency) == (1500.0, 1500.0, "usd")
    assert fixed.url.endswith("~01abc")
    assert fixed.client_country == "United States"

    hourly = channel._to_job({
        "id": "2", "title": "Hourly work", "description": "d", "ciphertext": "~02def",
        "hourlyBudgetMin": {"rawValue": "40", "currency": "USD"},
        "hourlyBudgetMax": {"rawValue": "70", "currency": "USD"},
        "totalApplicants": 3, "client": {},
    })
    assert (hourly.budget_low, hourly.budget_high) == (40.0, 70.0)


def test_a_posting_feeds_straight_into_the_bidder(cfg, ledger, provider):
    """Discovery and scoring must speak the same shape."""
    from earner import approval
    from earner.agents.bidding import BiddingAgent
    from conftest import FakeLLM

    job = UpworkChannel(cfg)._to_job({
        "id": "3", "title": "Automate quote desk", "description": "tests required",
        "ciphertext": "~03ghi", "amount": {"rawValue": "3000", "currency": "USD"},
        "totalApplicants": 6, "client": {},
    })
    bidder = BiddingAgent(cfg, ledger, provider, approval.AutoGate(), FakeLLM([]))
    verdict = bidder.score(job.as_posting())
    assert verdict.worth_bidding is True
    assert verdict.currency == "usd"


def test_graphql_errors_are_raised_not_swallowed(cfg, monkeypatch):
    """A wrong query name must never look like 'no jobs found'."""
    channel = UpworkChannel(_cfg(cfg, upwork_client_id="c", upwork_client_secret="s"))
    monkeypatch.setattr(channel, "access_token", lambda: "tok")

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(
            {"errors": [{"message": "Cannot query field 'nope'"}]}
        ).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResp())
    with pytest.raises(UpworkAuthError, match="Cannot query field"):
        channel.graphql("query { nope }")

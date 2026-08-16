"""Upwork channel — authorised job discovery over the official GraphQL API.

What this can do: find real open postings through Upwork's own API, with an
OAuth2 grant you authorise yourself. No scraping, no ToS grey area.

What it cannot do, and no library can: **submit a proposal.** Upwork's public
GraphQL API exposes no mutation for applying to a job or spending Connects.
That is deliberate on their part — bid spam is the platform's largest trust
problem and an open submission API would make it worse. Anything advertising
"auto-submit proposals" is going around the official API, and the cost of that
is your account.

So the division of labour is: this channel finds the work, the bidder drafts
the proposal, and you paste it. Endpoints and the auth flow below are taken
from Upwork's own python-upwork-oauth2 SDK.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BASE_HOST = "https://www.upwork.com"
GQL_ENDPOINT = "https://api.upwork.com/graphql"
AUTHORIZE_URL = f"{BASE_HOST}/ab/account-security/oauth2/authorize"
TOKEN_URL = f"{BASE_HOST}/api/v3/oauth2/token"

# The marketplace search query. Upwork's schema explorer (API Center, behind
# your login) is the authority; if this name is wrong for your key's scopes the
# API says so plainly and `search_jobs` surfaces that error rather than
# returning an empty list, so a wrong guess is a 30-second fix and never a
# silent no-op.
JOB_SEARCH_QUERY = """
query marketplaceJobPostings($filter: MarketplaceJobPostingsSearchFilter,
                             $pagination: PaginationInput) {
  marketplaceJobPostingsSearch(marketPlaceJobFilter: $filter,
                               searchType: USER_JOBS_SEARCH,
                               sortAttributes: [{field: RECENCY}]) {
    totalCount
    edges {
      node {
        id
        title
        description
        ciphertext
        duration
        engagement
        amount { rawValue currency }
        hourlyBudgetMin { rawValue currency }
        hourlyBudgetMax { rawValue currency }
        totalApplicants
        client { totalSpent { rawValue } totalHires location { country } }
        createdDateTime
      }
    }
  }
}
"""


class NotSupported(RuntimeError):
    """The Upwork API does not expose this operation."""


class UpworkAuthError(RuntimeError):
    """The grant is missing, expired beyond refresh, or lacks the scope."""


@dataclass
class UpworkJob:
    """One posting, normalised into the shape the bidder scores."""

    id: str
    title: str
    description: str
    url: str
    budget_low: float
    budget_high: float
    currency: str
    bid_count: int
    client_country: str
    client_spent: float

    def as_posting(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "budget_low": self.budget_low,
            "budget_high": self.budget_high,
            "currency": self.currency,
            "bid_count": self.bid_count,
            "requirements": self.description[:4000],
            "acceptance_criteria": "",
            "closes_in": "unknown",
        }


class UpworkChannel:
    def __init__(self, cfg):
        self.cfg = cfg
        self.client_id = getattr(cfg, "upwork_client_id", None)
        self.client_secret = getattr(cfg, "upwork_client_secret", None)
        self.redirect_uri = getattr(cfg, "upwork_redirect_uri", None) or "https://localhost/callback"
        self.token_path = Path(cfg.workdir) / "upwork_token.json"

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def enabled(self) -> bool:
        return self.configured and self.token_path.exists()

    # ------------------------------------------------------------------ oauth

    def authorize_url(self) -> str:
        """Step 1: you open this, approve, and Upwork redirects back with a code."""
        if not self.configured:
            raise UpworkAuthError("Set UPWORK_CLIENT_ID and UPWORK_CLIENT_SECRET first")
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
        }
        return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str) -> dict:
        """Step 2: trade the code for tokens and store them."""
        token = self._token_request({
            "grant_type": "authorization_code",
            "code": code.strip(),
            "redirect_uri": self.redirect_uri,
        })
        self._store(token)
        return token

    def _token_request(self, payload: dict) -> dict:
        data = urllib.parse.urlencode({
            **payload,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode()
        req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:400]
            raise UpworkAuthError(f"Token request rejected ({exc.code}): {body}") from exc

    def _store(self, token: dict) -> None:
        token["expires_at"] = time.time() + int(token.get("expires_in", 3600)) - 60
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(token, indent=2))
        os.chmod(self.token_path, 0o600)

    def access_token(self) -> str:
        """Current token, refreshed automatically when it has expired."""
        if not self.token_path.exists():
            raise UpworkAuthError("Not authorised yet — run `earner connect --channel upwork`")
        token = json.loads(self.token_path.read_text())
        if time.time() < token.get("expires_at", 0):
            return token["access_token"]
        if not token.get("refresh_token"):
            raise UpworkAuthError("Token expired and no refresh token — re-authorise")
        refreshed = self._token_request({
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
        })
        # Upwork may omit the refresh token on refresh; keep the existing one.
        refreshed.setdefault("refresh_token", token["refresh_token"])
        self._store(refreshed)
        return refreshed["access_token"]

    # ---------------------------------------------------------------- graphql

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        req = urllib.request.Request(GQL_ENDPOINT, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.access_token()}")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise UpworkAuthError(f"Upwork API returned {exc.code}: {detail}") from exc

        if payload.get("errors"):
            # Surfaced rather than swallowed: a wrong query name or a missing
            # scope must not look like "no jobs found".
            messages = "; ".join(e.get("message", str(e)) for e in payload["errors"])
            raise UpworkAuthError(f"GraphQL error: {messages}")
        return payload.get("data") or {}

    def search_jobs(self, query: str, *, limit: int = 20) -> list[UpworkJob]:
        """Find open postings matching a search term."""
        data = self.graphql(
            JOB_SEARCH_QUERY,
            {"filter": {"searchExpression_eq": query, "pagination_eq": {"first": limit}}},
        )
        search = data.get("marketplaceJobPostingsSearch") or {}
        out = []
        for edge in search.get("edges", []):
            node = edge.get("node") or {}
            out.append(self._to_job(node))
        return out

    def _to_job(self, node: dict) -> UpworkJob:
        fixed = node.get("amount") or {}
        low = node.get("hourlyBudgetMin") or {}
        high = node.get("hourlyBudgetMax") or {}
        client = node.get("client") or {}

        # Fixed-price postings carry `amount`; hourly ones carry a min/max rate.
        if fixed.get("rawValue"):
            budget_low = budget_high = float(fixed["rawValue"])
            currency = fixed.get("currency") or "USD"
        else:
            budget_low = float(low.get("rawValue") or 0)
            budget_high = float(high.get("rawValue") or 0)
            currency = high.get("currency") or low.get("currency") or "USD"

        cipher = node.get("ciphertext") or node.get("id") or ""
        return UpworkJob(
            id=node.get("id", ""),
            title=node.get("title", ""),
            description=node.get("description", "") or "",
            url=f"{BASE_HOST}/jobs/{cipher}" if cipher else BASE_HOST,
            budget_low=budget_low,
            budget_high=budget_high,
            currency=(currency or "USD").lower(),
            bid_count=int(node.get("totalApplicants") or 0),
            client_country=((client.get("location") or {}).get("country") or ""),
            client_spent=float((client.get("totalSpent") or {}).get("rawValue") or 0),
        )

    # --------------------------------------------------------------- not ours

    def submit_proposal(self, *_args, **_kwargs):
        raise NotSupported(
            "Upwork's public GraphQL API has no mutation for submitting a proposal — this is "
            "deliberate, to limit bid spam. Services claiming to auto-submit bypass the official "
            "API and put your account at risk. The bidder drafts it; you paste it."
        )

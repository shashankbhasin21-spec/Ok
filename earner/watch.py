"""The speed engine: be first, because first is the only edge available here.

Everything else in this repository tried to find an edge in prediction — which
market will move, which setup wins. All of it measured negative, because the
people on the other side of those trades are better resourced than we are.

This is a different kind of edge, and it is the only one in the project that
is both real and unguarded:

    A client receives their first proposal within about three hours of posting.
    The highest-return habit for a freelancer with no reviews is reaching a job
    inside the first hour, before the crowd arrives and a zero-review profile
    gets buried under proven ones. Ten tailored proposals to jobs under an hour
    old beat fifty generic ones to stale listings.

Nobody has a latency advantage over you here. There is no colocation, no order
book, no firm with a faster wire. There is only whether you were awake. A
program is awake at 03:00 and a person is not, and that is the entire thesis
of this module.

So `score()` is built around age first and everything else second. A perfect
match posted nine hours ago is worth less than a decent match posted nine
minutes ago, and the ranking says so out loud.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# How fast a posting stops being worth answering. At 60 minutes freshness is
# ~0.37, at two hours ~0.14, at four hours ~0.02. The decay constant is set so
# that "the first hour" is where nearly all the value sits, which is what the
# evidence says rather than what feels generous.
FRESHNESS_HALFLIFE_MINUTES = 60.0
STALE_AFTER_MINUTES = 360.0        # six hours: past this, do not bother


@dataclass
class Opening:
    """One job posting, from whatever source found it."""

    title: str
    url: str
    source: str = ""
    description: str = ""
    budget_low: float = 0.0
    budget_high: float = 0.0
    currency: str = "usd"
    bids: int = 0
    posted_at: float = 0.0          # epoch seconds; 0 means unknown
    client_country: str = ""
    payment_verified: bool = False
    skills: list = field(default_factory=list)

    @property
    def ref(self) -> str:
        return "job-" + hashlib.sha256(self.url.encode()).hexdigest()[:12]

    def age_minutes(self, now: float | None = None) -> float:
        """Minutes since posting. Unknown age is treated as two hours old.

        Treated as *old* rather than new on purpose: an unknown timestamp
        should not let a stale posting jump the queue ahead of one that is
        provably fresh.
        """
        if not self.posted_at:
            return 120.0
        return max(0.0, ((now or time.time()) - self.posted_at) / 60.0)

    def freshness(self, now: float | None = None) -> float:
        """1.0 at the moment of posting, decaying exponentially. Never negative."""
        age = self.age_minutes(now)
        if age >= STALE_AFTER_MINUTES:
            return 0.0
        return math.exp(-age / FRESHNESS_HALFLIFE_MINUTES)


# Rough conversion, used only to compare postings on one scale.
USD_PER = {"usd": 1.0, "eur": 1.08, "gbp": 1.27, "aud": 0.66,
           "cad": 0.73, "sgd": 0.74, "inr": 1 / 96.2}


@dataclass
class Profile:
    """What you can actually build, and what you will actually take.

    `floor_usd` defaults to 100 rather than the 150 the bidding agent used.
    That is a deliberate cold-start setting: a client risking $100-$500 is far
    more willing to take a chance on a profile with no reviews, and the first
    three jobs are for the reviews, not the money. Raise it once the profile
    has a history.
    """

    skills: list = field(default_factory=lambda: [
        "python", "automation", "api", "integration", "scraping", "data",
        "etl", "pipeline", "llm", "openai", "anthropic", "claude", "gpt",
        "agent", "workflow", "n8n", "zapier", "make.com", "webhook",
        "sqlite", "postgres", "pandas", "backtest", "trading", "fastapi",
        "reconciliation", "invoice", "pdf", "ocr", "extraction", "dashboard",
    ])
    avoid: list = field(default_factory=lambda: [
        "wordpress", "shopify theme", "logo", "photoshop", "video editing",
        "seo article", "content writing", "data entry", "virtual assistant",
        "unity", "unreal", "android app", "ios app", "flutter",
    ])
    floor_usd: float = 100.0
    ceiling_bids: int = 20          # tighter than the old 45: bids proxy for age
    prefer_countries: list = field(default_factory=lambda: [
        "united states", "united kingdom", "canada", "australia",
        "germany", "netherlands", "switzerland", "singapore", "ireland",
    ])


@dataclass
class Rank:
    score: float
    opening: Opening
    reasons: list = field(default_factory=list)
    blockers: list = field(default_factory=list)

    @property
    def worth_bidding(self) -> bool:
        return not self.blockers and self.score > 0


def _words(text: str) -> set:
    return set(re.findall(r"[a-z0-9.+#]+", text.lower()))


def fit(opening: Opening, profile: Profile) -> tuple:
    """How well this matches what you build. Returns (0-1, matched, avoided)."""
    haystack = _words(f"{opening.title} {opening.description} {' '.join(opening.skills)}")
    blob = f"{opening.title} {opening.description}".lower()

    matched = [s for s in profile.skills if s in haystack or s in blob]
    avoided = [a for a in profile.avoid if a in blob]
    if not matched:
        return 0.0, matched, avoided
    # Saturating: five relevant skills is a strong match and ten is not twice
    # as strong, so the score cannot be gamed by a posting that lists forty.
    return min(1.0, len(matched) / 5.0), matched, avoided


def score(opening: Opening, profile: Profile | None = None,
          now: float | None = None) -> Rank:
    """Rank one posting. Freshness dominates, deliberately.

    The multiplication is the point: a stale posting scores near zero however
    good the match, and a poor match scores near zero however fresh. Both have
    to be true, which is what a shortlist worth acting on requires.
    """
    profile = profile or Profile()
    blockers, reasons = [], []

    age = opening.age_minutes(now)
    freshness = opening.freshness(now)
    if freshness <= 0:
        blockers.append(f"posted {age / 60:.1f}h ago — the crowd has been and gone")

    quality, matched, avoided = fit(opening, profile)
    if avoided:
        blockers.append(f"outside what you build: {', '.join(avoided[:3])}")
    if quality <= 0:
        blockers.append("no overlap with your skills")

    rate = USD_PER.get(opening.currency.lower(), 1.0)
    high_usd = opening.budget_high * rate
    if opening.budget_high and high_usd < profile.floor_usd:
        blockers.append(f"${high_usd:,.0f} top of range is below your ${profile.floor_usd:,.0f} floor")
    if opening.bids > profile.ceiling_bids:
        blockers.append(f"{opening.bids} bids already — roughly {100 / max(opening.bids, 1):.1f}% per-bid odds")

    if age <= 60:
        reasons.append(f"posted {age:.0f} min ago — inside the first hour")
    elif freshness > 0:
        reasons.append(f"posted {age / 60:.1f}h ago")
    if matched:
        reasons.append("matches: " + ", ".join(matched[:5]))
    if high_usd:
        reasons.append(f"budget to ${high_usd:,.0f}")
    if opening.payment_verified:
        reasons.append("payment verified")
    if opening.client_country.lower() in profile.prefer_countries:
        reasons.append(f"client in {opening.client_country}")

    if blockers:
        return Rank(0.0, opening, reasons, blockers)

    # Competition: few bids is worth a lot, and it is largely a restatement of
    # freshness, so it is weighted gently to avoid counting age twice.
    competition = 1.0 / (1.0 + opening.bids / 10.0)
    trust = 1.15 if opening.payment_verified else 1.0
    geography = 1.2 if opening.client_country.lower() in profile.prefer_countries else 1.0

    return Rank(round(freshness * quality * competition * trust * geography, 4),
                opening, reasons, blockers)


def shortlist(openings: list, profile: Profile | None = None,
              now: float | None = None, limit: int = 10) -> list:
    """The postings worth writing a proposal for, best first."""
    ranks = [score(o, profile, now) for o in openings]
    live = [r for r in ranks if r.worth_bidding]
    live.sort(key=lambda r: -r.score)
    return live[:limit]


# ── memory ──────────────────────────────────────────────────────────────────

SCHEMA = """
create table if not exists seen (
    ref text primary key, url text, title text, source text,
    first_seen real, posted_at real, score real, acted integer default 0,
    payload text
);
"""


class Seen:
    """Which postings have already been surfaced.

    Without this the watcher re-alerts on the same job every poll, and a stream
    of duplicate alerts is one the operator stops reading — which costs exactly
    the speed the whole module exists to buy.
    """

    def __init__(self, path: Path | str = ".earner/watch.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def is_new(self, opening: Opening) -> bool:
        return self.db.execute("select 1 from seen where ref=?",
                               (opening.ref,)).fetchone() is None

    def remember(self, rank: Rank) -> None:
        o = rank.opening
        self.db.execute(
            "insert or ignore into seen (ref, url, title, source, first_seen,"
            " posted_at, score, payload) values (?,?,?,?,?,?,?,?)",
            (o.ref, o.url, o.title, o.source, time.time(), o.posted_at, rank.score,
             json.dumps({"reasons": rank.reasons, "budget_high": o.budget_high,
                         "currency": o.currency, "bids": o.bids})))
        self.db.commit()

    def mark_acted(self, ref: str) -> None:
        self.db.execute("update seen set acted=1 where ref=?", (ref,))
        self.db.commit()

    def stats(self, hours: float = 24.0) -> dict:
        cutoff = time.time() - hours * 3600
        row = self.db.execute(
            "select count(*) seen, sum(acted) acted from seen where first_seen > ?",
            (cutoff,)).fetchone()
        return {"seen": row["seen"] or 0, "acted": row["acted"] or 0}


# ── the loop ────────────────────────────────────────────────────────────────

def poll(sources: list, profile: Profile | None = None, memory: Seen | None = None,
         now: float | None = None, limit: int = 10) -> list:
    """One sweep: gather, drop what has been seen, rank, remember, return.

    `sources` are callables returning `list[Opening]`. Keeping them as plain
    callables means a new board is a function, not a subclass, and a broken one
    cannot take the sweep down with it.
    """
    profile = profile or Profile()
    found = []
    for source in sources:
        try:
            found.extend(source())
        except Exception as exc:      # noqa: BLE001 - one dead board must not end the sweep
            print(f"  ! {getattr(source, '__name__', source)}: {exc}")

    if memory is not None:
        found = [o for o in found if memory.is_new(o)]

    ranked = shortlist(found, profile, now, limit)
    if memory is not None:
        for rank in ranked:
            memory.remember(rank)
    return ranked


def render(ranks: list) -> str:
    """The alert. Written to be read on a phone at speed, because it will be."""
    if not ranks:
        return "nothing fresh"
    lines = []
    for i, rank in enumerate(ranks, 1):
        o = rank.opening
        age = o.age_minutes()
        budget = (f"${o.budget_high * USD_PER.get(o.currency.lower(), 1.0):,.0f}"
                  if o.budget_high else "no budget")
        lines.append(
            f"{i}. [{rank.score:.2f}] {o.title[:62]}\n"
            f"   {age:.0f} min old · {budget} · {o.bids} bids · {o.source}\n"
            f"   {o.url}\n"
            f"   {'; '.join(rank.reasons[:3])}")
    return "\n".join(lines)


def upwork_source(cfg):
    """Live Upwork postings, through the authorised API. Returns a callable."""
    from .channels.upwork import UpworkChannel

    channel = UpworkChannel(cfg)

    def fetch() -> list:
        if not channel.enabled:
            return []
        out = []
        for term in [t.strip() for t in cfg.upwork_searches.split(",") if t.strip()]:
            for job in channel.search_jobs(term, limit=20):
                posting = job.as_posting()
                out.append(Opening(
                    title=posting.get("title", ""), url=posting.get("url", ""),
                    source="upwork", description=posting.get("description", ""),
                    budget_low=float(posting.get("budget_low") or 0),
                    budget_high=float(posting.get("budget_high") or 0),
                    currency=posting.get("currency", "usd"),
                    bids=int(posting.get("bid_count") or 0),
                    posted_at=_parse_time(posting.get("posted_at")),
                    client_country=posting.get("client_country", ""),
                    payment_verified=bool(posting.get("payment_verified")),
                    skills=posting.get("skills") or []))
        return out

    fetch.__name__ = "upwork"
    return fetch


def _parse_time(value) -> float:
    if not value:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def watch(sources: list, *, profile: Profile | None = None, every: float = 300.0,
          workdir: str = ".earner", rounds: int | None = None,
          log=print, sleeper=time.sleep) -> int:
    """Poll forever, alerting on anything fresh and worth answering.

    Five minutes is the default interval. Faster does not help — the boards
    themselves do not update continuously — and it burns API quota that is
    better spent on the sweep that finds something.
    """
    memory = Seen(Path(workdir) / "watch.db")
    surfaced = 0
    try:
        round_number = 0
        while rounds is None or round_number < rounds:
            round_number += 1
            ranks = poll(sources, profile, memory)
            stamp = datetime.now(timezone.utc).astimezone().strftime("%H:%M")
            if ranks:
                surfaced += len(ranks)
                log(f"\n[{stamp}] {len(ranks)} worth answering\n{render(ranks)}")
            else:
                log(f"[{stamp}] nothing fresh")
            if rounds is None or round_number < rounds:
                sleeper(every)
    except KeyboardInterrupt:
        log("\nstopped")
    finally:
        memory.close()
    return surfaced

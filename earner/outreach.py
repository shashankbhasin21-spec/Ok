"""Cold outreach: research in, drafts out, replies tracked, invoice raised.

The ask was "find leads, mail them from my Gmail, handle the order and the
payment, and let me relax." This is that program. What it does not do is send
two hundred emails on the first morning, and the reason is practical rather
than cautious.

**Gmail will end the account.** Google's abuse systems watch for a burst of
near-identical mail to strangers who have no prior relationship with the
sender. A personal account that does that is suspended, and the suspension
takes the calendar, the drive and the login for every service that uses
"Sign in with Google" with it. The business would be over on day one, and not
because the pitch was wrong. So:

* the daily cap is 20 and configurable down, never silently up
* sends are spaced by a randomised delay, because a message every 1.000s is a
  signature no human produces
* nothing is ever sent twice to one address, which the database enforces
  rather than the code remembering
* two follow-ups, then the lead is closed forever
* every message carries a real opt-out, and an opt-out is permanent

The state machine is deliberately small:

    RESEARCHED -> DRAFTED -> SENT -> REPLIED -> QUOTED -> WON
                                  \\-> BOUNCED
                                  \\-> OPTED_OUT
                                  \\-> CLOSED   (two follow-ups, no answer)

A lead can only move forward, one step at a time, and `advance()` is the only
thing that moves it. That is what makes the pipeline resumable: the program
can be killed at any point and restarted, and it picks up from the database
rather than from anything held in memory.
"""

from __future__ import annotations

import email.utils
import hashlib
import imaplib
import random
import smtplib
import sqlite3
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
IMAP_HOST = "imap.gmail.com"

RESEARCHED, DRAFTED, SENT = "RESEARCHED", "DRAFTED", "SENT"
REPLIED, QUOTED, WON = "REPLIED", "QUOTED", "WON"
BOUNCED, OPTED_OUT, CLOSED = "BOUNCED", "OPTED_OUT", "CLOSED"

# Forward-only. A stage may move to any stage listed against it and nowhere
# else, so a bug cannot resurrect an opted-out lead into a send queue.
TRANSITIONS = {
    RESEARCHED: {DRAFTED, OPTED_OUT},
    DRAFTED: {SENT, OPTED_OUT, CLOSED},
    SENT: {REPLIED, BOUNCED, OPTED_OUT, CLOSED, SENT},   # SENT->SENT is a follow-up
    REPLIED: {QUOTED, CLOSED, OPTED_OUT},
    QUOTED: {WON, CLOSED, OPTED_OUT},
    WON: set(),
    BOUNCED: set(),
    OPTED_OUT: set(),
    CLOSED: set(),
}

DAILY_CAP = 20
FOLLOW_UP_DAYS = (3, 7)      # then stop, permanently
MAX_STEPS = len(FOLLOW_UP_DAYS)


class OutreachError(RuntimeError):
    """The pipeline refused to do something."""


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


@dataclass
class Prospect:
    """One company worth contacting, and the reason to believe it.

    `evidence` is not optional and is not decoration. It is the sentence that
    makes the email specific to them, and a pipeline that cannot produce one
    is a pipeline sending spam. `validate()` refuses the lead rather than
    letting a template fill the gap.
    """

    company: str
    email: str
    evidence: str
    contact_name: str = ""
    website: str = ""
    role: str = ""
    source: str = "research"
    notes: str = ""
    tags: list = field(default_factory=list)

    @property
    def ref(self) -> str:
        return "p-" + hashlib.sha256(self.email.strip().lower().encode()).hexdigest()[:12]

    def validate(self) -> "Prospect":
        if not self.company.strip():
            raise OutreachError("a prospect needs a company name")
        if "@" not in self.email or "." not in self.email.split("@")[-1]:
            raise OutreachError(f"{self.email!r} is not an address that can receive mail")
        if len(self.evidence.strip()) < 25:
            raise OutreachError(
                f"{self.company}: evidence is too thin to write a specific email. "
                "Without a reason to contact them, the only thing left to send is a "
                "template, which is worse than sending nothing.")
        return self


@dataclass
class Campaign:
    """What is being sold, to whom, and who it comes from."""

    name: str
    service: str
    from_name: str
    price_low: int
    price_high: int
    hook: str = "quick question"
    signature: str = ""
    reply_to: str = ""

    def subject(self, prospect: Prospect) -> str:
        """Short, lowercase, no pitch. The subject's only job is to be opened.

        `service` is a full sentence and reads as an advertisement in a subject
        line, so the subject uses a separate short `hook` instead. Anything
        over about six words, or with a price in it, is filtered or deleted
        before the body is ever seen.
        """
        first = prospect.company.split()[0].strip(",.")
        return f"{first} — {self.hook}"

    def body(self, prospect: Prospect) -> str:
        """A specific email, built from the evidence, with no adjectives to spare.

        Four sentences and a question. Length is a deliverability feature as
        much as a courtesy: long cold mail from an unknown sender reads as
        marketing to both the recipient and the spam filter.
        """
        greeting = f"Hi {prospect.contact_name.split()[0]}," if prospect.contact_name else "Hi,"
        return (
            f"{greeting}\n\n"
            f"{prospect.evidence.strip()}\n\n"
            f"I do one thing: {self.service}. Fixed price, "
            f"${self.price_low:,}-${self.price_high:,}, delivered in a week, and it is "
            f"read-only — I never touch your systems.\n\n"
            f"If it finds nothing, you pay nothing.\n\n"
            f"Worth 15 minutes to see whether it applies to you?\n\n"
            f"{self.signature or self.from_name}\n\n"
            f"---\n"
            f"Reply STOP and I will not contact you again."
        )

    def follow_up(self, prospect: Prospect, step: int) -> tuple:
        if step == 1:
            return (f"Re: {self.subject(prospect)}",
                    "Hi,\n\nBumping this once in case it got buried.\n\n"
                    "The offer is a fixed-price audit of your last 200 loads — I hand you "
                    "every discrepancy I find, and you pay nothing if I find none.\n\n"
                    f"{self.signature or self.from_name}\n\n---\n"
                    "Reply STOP and I will not contact you again.")
        return (f"Re: {self.subject(prospect)}",
                "Hi,\n\nLast note from me — I will not follow up again.\n\n"
                "If reconciliation ever becomes the bottleneck, my offer stands.\n\n"
                f"{self.signature or self.from_name}\n\n---\n"
                "Reply STOP and I will not contact you again.")


SCHEMA = """
create table if not exists prospects (
    ref text primary key, company text, email text unique, contact_name text,
    website text, role text, evidence text, notes text, source text, tags text,
    campaign text, stage text, step integer default 0,
    created_at real, updated_at real, next_action_at real,
    subject text, body text, message_id text, thread_ref text, reply_excerpt text
);
create table if not exists sends (
    id integer primary key autoincrement, ref text, at real, step integer,
    subject text, message_id text
);
create table if not exists events (
    id integer primary key autoincrement, ref text, at real, kind text, detail text
);
create table if not exists suppressions (
    email text primary key, at real, reason text
);
"""


class Pipeline:
    """The database is the program's memory. Nothing important lives in RAM."""

    def __init__(self, path: Path | str = ".earner/outreach.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def log(self, ref: str, kind: str, detail: str = "") -> None:
        self.db.execute("insert into events (ref, at, kind, detail) values (?,?,?,?)",
                        (ref, _now(), kind, detail))
        self.db.commit()

    # ── intake ─────────────────────────────────────────────────────────────

    def add(self, prospect: Prospect, campaign: str) -> str:
        """Add one prospect. Silently ignores duplicates and suppressed addresses.

        Silent rather than raising because the normal way to use this is to
        re-run a research batch that overlaps the last one, and a pipeline that
        halts on the first repeat is a pipeline nobody runs twice.
        """
        prospect.validate()
        address = prospect.email.strip().lower()
        if self.suppressed(address):
            return ""
        try:
            self.db.execute(
                "insert into prospects (ref, company, email, contact_name, website, role,"
                " evidence, notes, source, tags, campaign, stage, created_at, updated_at)"
                " values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (prospect.ref, prospect.company, address, prospect.contact_name,
                 prospect.website, prospect.role, prospect.evidence, prospect.notes,
                 prospect.source, ",".join(prospect.tags), campaign, RESEARCHED,
                 _now(), _now()))
            self.db.commit()
        except sqlite3.IntegrityError:
            return ""       # already known; not an error
        self.log(prospect.ref, "added", prospect.company)
        return prospect.ref

    def suppressed(self, address: str) -> bool:
        row = self.db.execute("select 1 from suppressions where email=?",
                              (address.strip().lower(),)).fetchone()
        return row is not None

    def suppress(self, address: str, reason: str = "opt-out") -> None:
        """Permanent. There is no path in this module that removes a suppression."""
        address = address.strip().lower()
        self.db.execute("insert or replace into suppressions (email, at, reason)"
                        " values (?,?,?)", (address, _now(), reason))
        self.db.execute("update prospects set stage=?, updated_at=?, next_action_at=null"
                        " where email=?", (OPTED_OUT, _now(), address))
        self.db.commit()
        self.log(address, "suppressed", reason)

    # ── state machine ──────────────────────────────────────────────────────

    def advance(self, ref: str, stage: str, **fields) -> None:
        """The only way a prospect changes stage. Refuses illegal transitions."""
        row = self.db.execute("select stage from prospects where ref=?", (ref,)).fetchone()
        if row is None:
            raise OutreachError(f"unknown prospect {ref}")
        current = row["stage"]
        if stage not in TRANSITIONS.get(current, set()):
            raise OutreachError(f"{ref}: cannot go {current} -> {stage}")
        sets = ", ".join(f"{k}=?" for k in fields)
        clause = f", {sets}" if sets else ""
        self.db.execute(f"update prospects set stage=?, updated_at=?{clause} where ref=?",
                        (stage, _now(), *fields.values(), ref))
        self.db.commit()
        self.log(ref, "stage", f"{current} -> {stage}")

    def at_stage(self, stage: str, campaign: str | None = None) -> list:
        sql = "select * from prospects where stage=?"
        args = [stage]
        if campaign:
            sql += " and campaign=?"
            args.append(campaign)
        return list(self.db.execute(sql + " order by created_at", args))

    def sent_today(self) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()
        row = self.db.execute("select count(*) c from sends where at > ?", (cutoff,)).fetchone()
        return row["c"]

    def remaining_today(self, cap: int = DAILY_CAP) -> int:
        return max(0, cap - self.sent_today())

    # ── drafting ───────────────────────────────────────────────────────────

    def draft_all(self, campaign: Campaign) -> int:
        """Write a subject and body for every researched prospect."""
        count = 0
        for row in self.at_stage(RESEARCHED, campaign.name):
            prospect = _to_prospect(row)
            self.advance(row["ref"], DRAFTED,
                         subject=campaign.subject(prospect),
                         body=campaign.body(prospect),
                         next_action_at=_now())
            count += 1
        return count

    def due_follow_ups(self, campaign: str | None = None) -> list:
        """Prospects whose follow-up date has arrived and who have steps left."""
        now = _now()
        out = []
        for row in self.at_stage(SENT, campaign):
            if row["step"] >= MAX_STEPS:
                continue
            if row["next_action_at"] and row["next_action_at"] <= now:
                out.append(row)
        return out

    def close_exhausted(self, campaign: str | None = None) -> int:
        """Two follow-ups and no answer: closed, and never contacted again."""
        count = 0
        for row in self.at_stage(SENT, campaign):
            if row["step"] >= MAX_STEPS and row["next_action_at"] and row["next_action_at"] <= _now():
                self.advance(row["ref"], CLOSED, next_action_at=None)
                count += 1
        return count

    def summary(self, campaign: str | None = None) -> dict:
        sql = "select stage, count(*) c from prospects"
        args = []
        if campaign:
            sql += " where campaign=?"
            args.append(campaign)
        rows = self.db.execute(sql + " group by stage", args)
        return {r["stage"]: r["c"] for r in rows}


def _to_prospect(row) -> Prospect:
    return Prospect(company=row["company"], email=row["email"],
                    evidence=row["evidence"] or "", contact_name=row["contact_name"] or "",
                    website=row["website"] or "", role=row["role"] or "",
                    source=row["source"] or "", notes=row["notes"] or "")


# ── sending ─────────────────────────────────────────────────────────────────
#
# Two gates, and they are independent on purpose. `live=False` builds the
# message and does not open a socket, which is what the tests and every dry run
# use. The environment variable is the second gate and exists so that a script
# calling `send(live=True)` still cannot mail a stranger unless a human set the
# variable in this shell.

SEND_CONFIRMATION = "OUTREACH_SEND_CONFIRMATION"
SEND_PHRASE = "I_UNDERSTAND_THIS_EMAILS_REAL_PEOPLE"


def sending_enabled(env: dict | None = None) -> bool:
    import os
    env = env if env is not None else os.environ
    return env.get(SEND_CONFIRMATION, "").strip() == SEND_PHRASE


def build_message(*, to: str, subject: str, body: str, from_address: str,
                  from_name: str = "", reply_to: str = "",
                  in_reply_to: str = "") -> EmailMessage:
    message = EmailMessage()
    message["To"] = to
    message["From"] = email.utils.formataddr((from_name, from_address)) if from_name else from_address
    message["Subject"] = subject
    message["Date"] = email.utils.formatdate(localtime=True)
    message["Message-ID"] = email.utils.make_msgid(domain=from_address.split("@")[-1])
    if reply_to:
        message["Reply-To"] = reply_to
    if in_reply_to:
        # Threading a follow-up onto the original is worth more than it looks:
        # it lands in the existing conversation instead of as a second cold mail.
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(body)
    return message


def send(message: EmailMessage, *, user: str, app_password: str,
         live: bool = False, env: dict | None = None) -> str:
    """Put one message on the wire. Returns its Message-ID.

    Refuses unless both gates are open. A dry run returns the Message-ID
    without connecting, so the whole pipeline can be exercised end to end
    without a single real email leaving the machine.
    """
    if not live:
        return message["Message-ID"]
    if not sending_enabled(env):
        raise OutreachError(
            f"refusing to send: set {SEND_CONFIRMATION}={SEND_PHRASE} in this shell. "
            "This is real mail to real strangers from your own address, and it "
            "cannot be recalled.")
    password = (app_password or "").replace(" ", "")
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30, context=context) as smtp:
        smtp.login(user, password)
        smtp.send_message(message)
    return message["Message-ID"]


def run_sends(pipeline: Pipeline, campaign: Campaign, *, user: str, app_password: str,
              cap: int = DAILY_CAP, live: bool = False, pause: tuple = (25, 90),
              sleeper=time.sleep, log=print) -> int:
    """Send today's batch: new drafts first, then follow-ups that came due.

    New mail before follow-ups because a follow-up to someone who never got the
    first message is nonsense, and because the top of the funnel is the thing
    that runs out.
    """
    budget = min(cap, pipeline.remaining_today(cap))
    if budget <= 0:
        log(f"daily cap of {cap} already used — nothing sent")
        return 0

    # The batch is fixed before the loop so the pause can be skipped after the
    # last message. Comparing against the daily cap instead left a 25-90 second
    # sleep at the end of every run, which does nothing except make a scheduled
    # job look hung.
    batch = pipeline.at_stage(DRAFTED, campaign.name)[:budget]
    follow_ups = pipeline.due_follow_ups(campaign.name)[:max(0, budget - len(batch))]
    total = len(batch) + len(follow_ups)

    sent = 0
    for row in batch:
        if pipeline.suppressed(row["email"]):
            pipeline.advance(row["ref"], OPTED_OUT)
            continue
        message = build_message(to=row["email"], subject=row["subject"], body=row["body"],
                                from_address=user, from_name=campaign.from_name,
                                reply_to=campaign.reply_to)
        message_id = send(message, user=user, app_password=app_password, live=live)
        pipeline.db.execute("insert into sends (ref, at, step, subject, message_id)"
                            " values (?,?,?,?,?)",
                            (row["ref"], _now(), 0, row["subject"], message_id))
        pipeline.advance(row["ref"], SENT, step=0, message_id=message_id,
                         next_action_at=_now() + FOLLOW_UP_DAYS[0] * 86400)
        sent += 1
        log(f"  -> {row['company']:<34} {row['email']}")
        if sent < total:
            sleeper(random.uniform(*pause))

    for row in follow_ups:
        if pipeline.suppressed(row["email"]):
            pipeline.advance(row["ref"], OPTED_OUT)
            continue
        step = row["step"] + 1
        subject, body = campaign.follow_up(_to_prospect(row), step)
        message = build_message(to=row["email"], subject=subject, body=body,
                                from_address=user, from_name=campaign.from_name,
                                reply_to=campaign.reply_to,
                                in_reply_to=row["message_id"] or "")
        message_id = send(message, user=user, app_password=app_password, live=live)
        pipeline.db.execute("insert into sends (ref, at, step, subject, message_id)"
                            " values (?,?,?,?,?)", (row["ref"], _now(), step, subject, message_id))
        next_at = (_now() + (FOLLOW_UP_DAYS[step] - FOLLOW_UP_DAYS[step - 1]) * 86400
                   if step < MAX_STEPS else _now() + 7 * 86400)
        pipeline.advance(row["ref"], SENT, step=step, next_action_at=next_at)
        sent += 1
        log(f"  -> {row['company']:<34} follow-up {step}")
        if sent < total:
            sleeper(random.uniform(*pause))

    return sent


# ── replies ─────────────────────────────────────────────────────────────────

STOP_WORDS = ("stop", "unsubscribe", "remove me", "opt out", "opt-out",
              "do not contact", "take me off")


def looks_like_optout(text: str) -> bool:
    lowered = " ".join(text.lower().split())[:400]
    return any(word in lowered for word in STOP_WORDS)


def check_replies(pipeline: Pipeline, *, user: str, app_password: str,
                  live: bool = False, log=print) -> int:
    """Scan the inbox for replies from anyone we mailed.

    An opt-out is honoured before anything else in the message is considered,
    including a reply that also sounds interested. Someone who writes "sounds
    good but please remove me" gets removed.
    """
    if not live:
        return 0
    addresses = {r["email"]: r["ref"] for r in
                 pipeline.db.execute("select email, ref from prospects where stage=?", (SENT,))}
    if not addresses:
        return 0

    found = 0
    password = (app_password or "").replace(" ", "")
    conn = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        conn.login(user, password)
        conn.select("INBOX")
        for address, ref in addresses.items():
            status, data = conn.search(None, f'(FROM "{address}")')
            if status != "OK" or not data or not data[0]:
                continue
            newest = data[0].split()[-1]
            status, raw = conn.fetch(newest, "(BODY.PEEK[TEXT])")
            text = ""
            if status == "OK" and raw and isinstance(raw[0], tuple):
                text = raw[0][1].decode(errors="replace")
            if looks_like_optout(text):
                pipeline.suppress(address, "replied STOP")
                log(f"  opted out: {address}")
                continue
            pipeline.advance(ref, REPLIED, reply_excerpt=text[:500], next_action_at=None)
            log(f"  REPLY from {address}")
            found += 1
    finally:
        try:
            conn.logout()
        except Exception:       # noqa: BLE001 - logout failure must not lose the replies
            pass
    return found


# ── the order and the money ─────────────────────────────────────────────────
#
# A reply is not revenue. This is the bridge from "someone answered" to "an
# invoice exists", and it runs through the ledger and payment provider that
# were already in this repository rather than a second set of tables. One
# ledger means one number for how much has actually been earned.


def quote(pipeline: Pipeline, ref: str, *, ledger, provider, campaign: Campaign,
          amount_cents: int, currency: str = "usd", agent: str = "outreach") -> dict:
    """Turn a replied prospect into a job and a payable invoice.

    Only a REPLIED prospect can be quoted, which the state machine enforces.
    Invoicing someone who never answered is the single worst thing this program
    could do to the sender's reputation, so it is made structurally impossible
    rather than merely discouraged.
    """
    row = pipeline.db.execute("select * from prospects where ref=?", (ref,)).fetchone()
    if row is None:
        raise OutreachError(f"unknown prospect {ref}")
    if row["stage"] != REPLIED:
        raise OutreachError(
            f"{row['company']} is {row['stage']}, not {REPLIED} — only someone who "
            "answered can be invoiced")

    opportunity_id = ledger.record_opportunity(
        agent=agent, source="outreach", external_ref=ref,
        payload={"company": row["company"], "email": row["email"],
                 "campaign": row["campaign"], "evidence": row["evidence"]})

    job = ledger.create_job(agent=agent, title=f"{campaign.service} — {row['company']}",
                            quote_cents=amount_cents, currency=currency,
                            opportunity_id=opportunity_id)

    handle = provider.create_invoice(
        customer_email=row["email"],
        description=f"{campaign.service} for {row['company']}",
        amount_cents=amount_cents, currency=currency,
        metadata={"prospect_ref": ref, "job_id": job.id, "campaign": row["campaign"]})

    invoice = ledger.record_invoice(job_id=job.id, provider=handle.provider,
                                    provider_ref=handle.provider_ref,
                                    amount_cents=handle.amount_cents,
                                    currency=handle.currency, status=handle.status,
                                    url=handle.url)
    pipeline.advance(ref, QUOTED, notes=f"invoice {handle.provider_ref}")
    return {"job_id": job.id, "invoice_id": invoice.id, "url": handle.url,
            "provider_ref": handle.provider_ref, "amount_cents": amount_cents}


def collect(pipeline: Pipeline, *, ledger, provider, log=print) -> int:
    """Check every open invoice and mark the paid ones WON.

    Payment is confirmed by asking the provider, never by the customer saying
    so in an email. That distinction is the whole reason this function exists
    separately from `check_replies`.
    """
    paid = 0
    for invoice in ledger.open_invoices():
        status = provider.fetch_status(invoice.provider_ref)
        if status not in ("paid", "succeeded"):
            continue
        settlement = provider.fetch_settlement(invoice.provider_ref)
        if settlement is None:
            continue
        if not ledger.settle(invoice=invoice, provider_event_id=settlement.event_id,
                             amount_cents=settlement.amount_cents,
                             currency=settlement.currency):
            continue        # this payment event was already applied
        row = pipeline.db.execute(
            "select ref, company from prospects where notes like ?",
            (f"%{invoice.provider_ref}%",)).fetchone()
        if row and pipeline.db.execute(
                "select stage from prospects where ref=?", (row["ref"],)).fetchone()["stage"] == QUOTED:
            pipeline.advance(row["ref"], WON)
            log(f"  PAID: {row['company']}  {settlement.amount_cents / 100:,.2f} "
                f"{settlement.currency.upper()}")
        paid += 1
    return paid

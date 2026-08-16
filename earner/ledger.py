"""The ledger — the only component allowed to say money was earned.

Two rules make the numbers trustworthy:

1. Revenue is recorded *only* from a payment the provider has confirmed as
   settled. Nothing an agent believes, promises, or invoices counts.
2. Settlement is idempotent on the provider's own event id, so replaying a
   webhook or re-polling an invoice can never double-count a dollar.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    agent         TEXT NOT NULL,
    source        TEXT NOT NULL,
    external_ref  TEXT NOT NULL,
    payload       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'new',
    created_at    REAL NOT NULL,
    UNIQUE (agent, external_ref)
);

CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id  INTEGER REFERENCES opportunities(id),
    agent           TEXT NOT NULL,
    title           TEXT NOT NULL,
    status          TEXT NOT NULL,
    quote_cents     INTEGER NOT NULL DEFAULT 0,
    currency        TEXT NOT NULL DEFAULT 'usd',
    cost_cents      INTEGER NOT NULL DEFAULT 0,
    deliverable     TEXT,
    notes           TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS invoices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        INTEGER NOT NULL REFERENCES jobs(id),
    provider      TEXT NOT NULL,
    provider_ref  TEXT NOT NULL,
    amount_cents  INTEGER NOT NULL,
    currency      TEXT NOT NULL,
    status        TEXT NOT NULL,
    url           TEXT,
    created_at    REAL NOT NULL,
    UNIQUE (provider, provider_ref)
);

-- Settled money only. One row per confirmed provider event.
CREATE TABLE IF NOT EXISTS payments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id        INTEGER NOT NULL REFERENCES invoices(id),
    provider          TEXT NOT NULL,
    provider_event_id TEXT NOT NULL,
    amount_cents      INTEGER NOT NULL,
    currency          TEXT NOT NULL,
    confirmed_at      REAL NOT NULL,
    UNIQUE (provider, provider_event_id)
);

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    entity    TEXT NOT NULL,
    entity_id INTEGER,
    kind      TEXT NOT NULL,
    data      TEXT NOT NULL,
    ts        REAL NOT NULL
);
"""


# Job lifecycle.
DRAFT = "draft"
AWAITING_APPROVAL = "awaiting_approval"
AWAITING_PAYMENT = "awaiting_payment"
IN_PROGRESS = "in_progress"
DELIVERED = "delivered"
REJECTED = "rejected"
FAILED = "failed"


@dataclass
class Job:
    id: int
    agent: str
    title: str
    status: str
    quote_cents: int
    currency: str
    cost_cents: int
    deliverable: str | None
    notes: str | None
    opportunity_id: int | None = None

    @property
    def margin_cents(self) -> int:
        return self.quote_cents - self.cost_cents


@dataclass
class Invoice:
    id: int
    job_id: int
    provider: str
    provider_ref: str
    amount_cents: int
    currency: str
    status: str
    url: str | None


class Ledger:
    def __init__(self, path: Path | str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- events

    def log(self, entity: str, entity_id: int | None, kind: str, **data: Any) -> None:
        self.conn.execute(
            "INSERT INTO events (entity, entity_id, kind, data, ts) VALUES (?,?,?,?,?)",
            (entity, entity_id, kind, json.dumps(data, default=str), time.time()),
        )
        self.conn.commit()

    def recent_events(self, limit: int = 20) -> list[sqlite3.Row]:
        return list(
            self.conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        )

    # --------------------------------------------------------- opportunities

    def record_opportunity(
        self, agent: str, source: str, external_ref: str, payload: dict
    ) -> int | None:
        """Insert an opportunity. Returns None if it was already seen.

        Deduplication is on (agent, external_ref) so re-scanning an inbox
        never re-bills a customer for the same request.
        """
        try:
            cur = self.conn.execute(
                "INSERT INTO opportunities (agent, source, external_ref, payload, status, created_at)"
                " VALUES (?,?,?,?,'new',?)",
                (agent, source, external_ref, json.dumps(payload, default=str), time.time()),
            )
            self.conn.commit()
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            return None

    def opportunity_payload(self, opportunity_id: int | None) -> dict:
        if opportunity_id is None:
            return {}
        row = self.conn.execute(
            "SELECT payload FROM opportunities WHERE id=?", (opportunity_id,)
        ).fetchone()
        return json.loads(row["payload"]) if row else {}

    def set_opportunity_status(self, opportunity_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE opportunities SET status=? WHERE id=?", (status, opportunity_id)
        )
        self.conn.commit()

    # ------------------------------------------------------------------ jobs

    def create_job(
        self,
        agent: str,
        title: str,
        quote_cents: int,
        currency: str,
        opportunity_id: int | None = None,
        status: str = DRAFT,
    ) -> Job:
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO jobs (opportunity_id, agent, title, status, quote_cents, currency,"
            " cost_cents, created_at, updated_at) VALUES (?,?,?,?,?,?,0,?,?)",
            (opportunity_id, agent, title, status, quote_cents, currency, now, now),
        )
        self.conn.commit()
        job = self.get_job(int(cur.lastrowid))
        self.log("job", job.id, "created", agent=agent, title=title, quote_cents=quote_cents)
        return job

    def get_job(self, job_id: int) -> Job:
        row = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"no job {job_id}")
        return Job(
            id=row["id"],
            agent=row["agent"],
            title=row["title"],
            status=row["status"],
            quote_cents=row["quote_cents"],
            currency=row["currency"],
            cost_cents=row["cost_cents"],
            deliverable=row["deliverable"],
            notes=row["notes"],
            opportunity_id=row["opportunity_id"],
        )

    def update_job(self, job_id: int, **fields: Any) -> Job:
        allowed = {"status", "quote_cents", "deliverable", "notes", "cost_cents", "title"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"cannot update job fields: {sorted(unknown)}")
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(
            f"UPDATE jobs SET {sets}, updated_at=? WHERE id=?",
            (*fields.values(), time.time(), job_id),
        )
        self.conn.commit()
        return self.get_job(job_id)

    def add_cost(self, job_id: int, cents: int) -> Job:
        """Accrue spend (LLM tokens, provider fees) against a job."""
        self.conn.execute(
            "UPDATE jobs SET cost_cents = cost_cents + ?, updated_at=? WHERE id=?",
            (cents, time.time(), job_id),
        )
        self.conn.commit()
        return self.get_job(job_id)

    def jobs(self, status: str | None = None, agent: str | None = None) -> list[Job]:
        sql, args = "SELECT id FROM jobs", []
        where = []
        if status:
            where.append("status=?")
            args.append(status)
        if agent:
            where.append("agent=?")
            args.append(agent)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id"
        return [self.get_job(r["id"]) for r in self.conn.execute(sql, args)]

    # -------------------------------------------------------------- invoices

    def record_invoice(
        self,
        job_id: int,
        provider: str,
        provider_ref: str,
        amount_cents: int,
        currency: str,
        status: str,
        url: str | None,
    ) -> Invoice:
        self.conn.execute(
            "INSERT OR IGNORE INTO invoices (job_id, provider, provider_ref, amount_cents,"
            " currency, status, url, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (job_id, provider, provider_ref, amount_cents, currency, status, url, time.time()),
        )
        self.conn.commit()
        inv = self.get_invoice_by_ref(provider, provider_ref)
        self.log("invoice", inv.id, "recorded", job_id=job_id, amount_cents=amount_cents, url=url)
        return inv

    def get_invoice_by_ref(self, provider: str, provider_ref: str) -> Invoice:
        row = self.conn.execute(
            "SELECT * FROM invoices WHERE provider=? AND provider_ref=?", (provider, provider_ref)
        ).fetchone()
        if row is None:
            raise KeyError(f"no invoice {provider}:{provider_ref}")
        return Invoice(
            id=row["id"],
            job_id=row["job_id"],
            provider=row["provider"],
            provider_ref=row["provider_ref"],
            amount_cents=row["amount_cents"],
            currency=row["currency"],
            status=row["status"],
            url=row["url"],
        )

    def set_invoice_status(self, invoice_id: int, status: str) -> None:
        self.conn.execute("UPDATE invoices SET status=? WHERE id=?", (status, invoice_id))
        self.conn.commit()

    def open_invoices(self) -> list[Invoice]:
        rows = self.conn.execute(
            "SELECT provider, provider_ref FROM invoices WHERE status NOT IN ('paid','void')"
        )
        return [self.get_invoice_by_ref(r["provider"], r["provider_ref"]) for r in rows]

    def invoices_for_job(self, job_id: int) -> list[Invoice]:
        rows = self.conn.execute(
            "SELECT provider, provider_ref FROM invoices WHERE job_id=?", (job_id,)
        )
        return [self.get_invoice_by_ref(r["provider"], r["provider_ref"]) for r in rows]

    # -------------------------------------------------------------- payments

    def settle(
        self,
        invoice: Invoice,
        provider_event_id: str,
        amount_cents: int,
        currency: str,
    ) -> bool:
        """Record confirmed money. Returns False if this event was already applied.

        This is the *only* path by which revenue enters the system.
        """
        try:
            self.conn.execute(
                "INSERT INTO payments (invoice_id, provider, provider_event_id, amount_cents,"
                " currency, confirmed_at) VALUES (?,?,?,?,?,?)",
                (
                    invoice.id,
                    invoice.provider,
                    provider_event_id,
                    amount_cents,
                    currency,
                    time.time(),
                ),
            )
        except sqlite3.IntegrityError:
            return False  # duplicate webhook / re-poll — already counted
        self.conn.execute("UPDATE invoices SET status='paid' WHERE id=?", (invoice.id,))
        self.conn.commit()
        self.log(
            "payment",
            invoice.id,
            "settled",
            job_id=invoice.job_id,
            amount_cents=amount_cents,
            provider_event_id=provider_event_id,
        )
        return True

    def is_paid(self, invoice_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM payments WHERE invoice_id=? LIMIT 1", (invoice_id,)
        ).fetchone()
        return row is not None

    def job_is_paid(self, job_id: int) -> bool:
        return any(self.is_paid(inv.id) for inv in self.invoices_for_job(job_id))

    # ------------------------------------------------------------ accounting

    def revenue_cents(self, agent: str | None = None) -> int:
        """Confirmed, settled revenue. Never includes invoiced-but-unpaid."""
        if agent is None:
            row = self.conn.execute("SELECT COALESCE(SUM(amount_cents),0) s FROM payments")
        else:
            row = self.conn.execute(
                "SELECT COALESCE(SUM(p.amount_cents),0) s FROM payments p"
                " JOIN invoices i ON i.id = p.invoice_id"
                " JOIN jobs j ON j.id = i.job_id WHERE j.agent = ?",
                (agent,),
            )
        return int(row.fetchone()["s"])

    def cost_cents(self, agent: str | None = None) -> int:
        if agent is None:
            row = self.conn.execute("SELECT COALESCE(SUM(cost_cents),0) s FROM jobs")
        else:
            row = self.conn.execute(
                "SELECT COALESCE(SUM(cost_cents),0) s FROM jobs WHERE agent=?", (agent,)
            )
        return int(row.fetchone()["s"])

    def outstanding_cents(self, agent: str | None = None) -> int:
        """Invoiced but not settled. Explicitly *not* revenue."""
        sql = (
            "SELECT COALESCE(SUM(i.amount_cents),0) s FROM invoices i"
            " JOIN jobs j ON j.id = i.job_id"
            " WHERE i.status NOT IN ('paid','void')"
        )
        args: tuple = ()
        if agent:
            sql += " AND j.agent=?"
            args = (agent,)
        return int(self.conn.execute(sql, args).fetchone()["s"])

    def agents_seen(self) -> list[str]:
        return [r["agent"] for r in self.conn.execute("SELECT DISTINCT agent FROM jobs ORDER BY agent")]

    def summary(self, agents: Iterable[str] | None = None) -> dict[str, dict[str, int]]:
        names = list(agents) if agents is not None else self.agents_seen()
        out: dict[str, dict[str, int]] = {}
        for name in names:
            revenue = self.revenue_cents(name)
            cost = self.cost_cents(name)
            out[name] = {
                "revenue_cents": revenue,
                "cost_cents": cost,
                "net_cents": revenue - cost,
                "outstanding_cents": self.outstanding_cents(name),
                "jobs": len(self.jobs(agent=name)),
                "delivered": len(self.jobs(status=DELIVERED, agent=name)),
            }
        out["TOTAL"] = {
            "revenue_cents": self.revenue_cents(),
            "cost_cents": self.cost_cents(),
            "net_cents": self.revenue_cents() - self.cost_cents(),
            "outstanding_cents": self.outstanding_cents(),
            "jobs": len(self.jobs()),
            "delivered": len(self.jobs(status=DELIVERED)),
        }
        return out

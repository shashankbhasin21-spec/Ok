"""Durable firm store: opportunities, agents, approvals, experiments, audit."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import OppStatus, TRANSITIONS

SCHEMA = """
CREATE TABLE IF NOT EXISTS firm_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_opps (
    id              TEXT PRIMARY KEY,
    source_url      TEXT NOT NULL DEFAULT '',
    external_id     TEXT NOT NULL,
    source          TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    posted_at       REAL,
    eligibility     TEXT NOT NULL DEFAULT '',
    budget_cents    INTEGER,
    budget_currency TEXT NOT NULL DEFAULT 'usd',
    scope           TEXT NOT NULL DEFAULT '',
    deadline        TEXT NOT NULL DEFAULT '',
    skills_json     TEXT NOT NULL DEFAULT '[]',
    client_signals  TEXT NOT NULL DEFAULT '',
    fit_score       REAL,
    fit_reasons     TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL,
    proposal_json   TEXT,
    proposal_version INTEGER NOT NULL DEFAULT 0,
    approval_id     TEXT,
    submission_key  TEXT,
    submission_receipt TEXT,
    project_id      TEXT,
    simulated       INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS approvals (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    summary         TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    amount_cents    INTEGER,
    status          TEXT NOT NULL DEFAULT 'pending',
    decided_by      TEXT,
    reason          TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    decided_at      REAL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id              TEXT PRIMARY KEY,
    role            TEXT NOT NULL,
    name            TEXT NOT NULL,
    status          TEXT NOT NULL,
    hypothesis      TEXT NOT NULL DEFAULT '',
    deliverable     TEXT NOT NULL DEFAULT '',
    parent_id       TEXT,
    token_budget    INTEGER NOT NULL DEFAULT 50000,
    tokens_used     INTEGER NOT NULL DEFAULT 0,
    cost_budget_cents INTEGER NOT NULL DEFAULT 200,
    cost_cents      INTEGER NOT NULL DEFAULT 0,
    time_budget_sec INTEGER NOT NULL DEFAULT 600,
    retries         INTEGER NOT NULL DEFAULT 0,
    max_retries     INTEGER NOT NULL DEFAULT 2,
    expires_at      REAL,
    started_at      REAL NOT NULL,
    finished_at     REAL,
    output_json     TEXT,
    error           TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    agent_run_id    TEXT,
    kind            TEXT NOT NULL,
    payload_json    TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'queued',
    idempotency_key TEXT NOT NULL UNIQUE,
    priority        INTEGER NOT NULL DEFAULT 100,
    result_json     TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    completed_at    REAL
);

CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,
    opportunity_id  TEXT NOT NULL,
    brief           TEXT NOT NULL,
    acceptance_json TEXT NOT NULL DEFAULT '[]',
    workspace_path  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    preview_path    TEXT,
    delivered_at    REAL,
    accepted_at     REAL,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS milestones (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    title           TEXT NOT NULL,
    acceptance      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    sort_order      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS experiments (
    id              TEXT PRIMARY KEY,
    hypothesis      TEXT NOT NULL,
    metric          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    bottleneck      TEXT NOT NULL,
    co_agent_id     TEXT,
    result_json     TEXT,
    created_at      REAL NOT NULL,
    expires_at      REAL NOT NULL,
    closed_at       REAL
);

CREATE TABLE IF NOT EXISTS standing_auth (
    id              TEXT PRIMARY KEY,
    platform        TEXT NOT NULL,
    service         TEXT NOT NULL,
    max_price_cents INTEGER NOT NULL,
    daily_volume    INTEGER NOT NULL,
    daily_spend_cents INTEGER NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_usage (
    day             TEXT NOT NULL,
    platform        TEXT NOT NULL,
    service         TEXT NOT NULL,
    volume          INTEGER NOT NULL DEFAULT 0,
    spend_cents     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, platform, service)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    actor           TEXT NOT NULL,
    action          TEXT NOT NULL,
    entity_type     TEXT NOT NULL DEFAULT '',
    entity_id       TEXT NOT NULL DEFAULT '',
    detail_json     TEXT NOT NULL DEFAULT '{}',
    redacted        INTEGER NOT NULL DEFAULT 1,
    ts              REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS firm_invoices (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    provider        TEXT NOT NULL,
    provider_ref    TEXT NOT NULL,
    amount_cents    INTEGER NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'usd',
    original_currency TEXT,
    fx_rate         REAL,
    fees_cents      INTEGER NOT NULL DEFAULT 0,
    lifecycle       TEXT NOT NULL,
    url             TEXT,
    simulated       INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    UNIQUE (provider, provider_ref)
);

CREATE TABLE IF NOT EXISTS firm_payments (
    id              TEXT PRIMARY KEY,
    invoice_id      TEXT NOT NULL,
    provider_event_id TEXT NOT NULL,
    amount_cents    INTEGER NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'usd',
    confirmed_at    REAL NOT NULL,
    UNIQUE (invoice_id, provider_event_id)
);

CREATE TABLE IF NOT EXISTS review_cycles (
    id              TEXT PRIMARY KEY,
    bottleneck      TEXT NOT NULL,
    findings_json   TEXT NOT NULL,
    experiment_id   TEXT,
    created_at      REAL NOT NULL
);
"""


SECRET_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "api_key",
        "bank",
        "iban",
        "upi",
        "account_number",
        "routing",
        "ssn",
        "card",
    }
)


def redact(obj: Any) -> Any:
    """Strip secret-looking fields from audit payloads."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if any(s in k.lower() for s in SECRET_KEYS):
                out[k] = "***REDACTED***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


@dataclass
class Opportunity:
    id: str
    source: str
    external_id: str
    title: str
    status: str
    source_url: str = ""
    description: str = ""
    posted_at: float | None = None
    eligibility: str = ""
    budget_cents: int | None = None
    budget_currency: str = "usd"
    scope: str = ""
    deadline: str = ""
    skills: list[str] | None = None
    client_signals: str = ""
    fit_score: float | None = None
    fit_reasons: list[str] | None = None
    proposal: dict | None = None
    proposal_version: int = 0
    approval_id: str | None = None
    submission_key: str | None = None
    submission_receipt: str | None = None
    project_id: str | None = None
    simulated: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "external_id": self.external_id,
            "title": self.title,
            "status": self.status,
            "source_url": self.source_url,
            "description": self.description,
            "posted_at": self.posted_at,
            "eligibility": self.eligibility,
            "budget_cents": self.budget_cents,
            "budget_currency": self.budget_currency,
            "scope": self.scope,
            "deadline": self.deadline,
            "skills": self.skills or [],
            "client_signals": self.client_signals,
            "fit_score": self.fit_score,
            "fit_reasons": self.fit_reasons or [],
            "proposal": self.proposal,
            "proposal_version": self.proposal_version,
            "approval_id": self.approval_id,
            "submission_key": self.submission_key,
            "submission_receipt": self.submission_receipt,
            "project_id": self.project_id,
            "simulated": self.simulated,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class IllegalTransition(ValueError):
    pass


class FirmStore:
    def __init__(self, path: Path | str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._init_meta()

    def _init_meta(self) -> None:
        defaults = {
            "paused": "0",
            "max_concurrent_agents": "4",
            "cost_circuit_breaker_cents": "5000",
            "owner_id": "owner",
            "milestone_cents": "2000000",
            "aspirational_rate_cents": "100000",
        }
        for k, v in defaults.items():
            self.conn.execute(
                "INSERT OR IGNORE INTO firm_meta (key, value) VALUES (?,?)", (k, v)
            )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- meta

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute(
            "SELECT value FROM firm_meta WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO firm_meta (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def is_paused(self) -> bool:
        return self.get_meta("paused") == "1"

    def set_paused(self, paused: bool) -> None:
        self.set_meta("paused", "1" if paused else "0")
        self.audit("owner", "pause" if paused else "resume", "firm", "global")

    # --------------------------------------------------------------- audit

    def audit(
        self,
        actor: str,
        action: str,
        entity_type: str = "",
        entity_id: str = "",
        **detail: Any,
    ) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (actor, action, entity_type, entity_id, detail_json, redacted, ts)"
            " VALUES (?,?,?,?,?,1,?)",
            (
                actor,
                action,
                entity_type,
                entity_id,
                json.dumps(redact(detail), default=str),
                time.time(),
            ),
        )
        self.conn.commit()

    def recent_audit(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    # --------------------------------------------------------- opportunities

    def _row_to_opp(self, row: sqlite3.Row) -> Opportunity:
        return Opportunity(
            id=row["id"],
            source=row["source"],
            external_id=row["external_id"],
            title=row["title"],
            status=row["status"],
            source_url=row["source_url"] or "",
            description=row["description"] or "",
            posted_at=row["posted_at"],
            eligibility=row["eligibility"] or "",
            budget_cents=row["budget_cents"],
            budget_currency=row["budget_currency"] or "usd",
            scope=row["scope"] or "",
            deadline=row["deadline"] or "",
            skills=json.loads(row["skills_json"] or "[]"),
            client_signals=row["client_signals"] or "",
            fit_score=row["fit_score"],
            fit_reasons=json.loads(row["fit_reasons"] or "[]"),
            proposal=json.loads(row["proposal_json"]) if row["proposal_json"] else None,
            proposal_version=row["proposal_version"] or 0,
            approval_id=row["approval_id"],
            submission_key=row["submission_key"],
            submission_receipt=row["submission_receipt"],
            project_id=row["project_id"],
            simulated=bool(row["simulated"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def import_opportunity(
        self,
        *,
        source: str,
        external_id: str,
        title: str,
        source_url: str = "",
        description: str = "",
        posted_at: float | None = None,
        eligibility: str = "",
        budget_cents: int | None = None,
        budget_currency: str = "usd",
        scope: str = "",
        deadline: str = "",
        skills: list[str] | None = None,
        client_signals: str = "",
        simulated: bool = False,
    ) -> Opportunity | None:
        """Insert opportunity. Returns None on duplicate (source, external_id)."""
        oid = f"opp_{uuid.uuid4().hex[:12]}"
        now = time.time()
        try:
            self.conn.execute(
                "INSERT INTO pipeline_opps ("
                " id, source_url, external_id, source, title, description, posted_at,"
                " eligibility, budget_cents, budget_currency, scope, deadline, skills_json,"
                " client_signals, status, simulated, created_at, updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    oid,
                    source_url,
                    external_id,
                    source,
                    title,
                    description,
                    posted_at,
                    eligibility,
                    budget_cents,
                    budget_currency.lower(),
                    scope,
                    deadline,
                    json.dumps(skills or []),
                    client_signals,
                    OppStatus.DISCOVERED.value,
                    1 if simulated else 0,
                    now,
                    now,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            return None
        self.audit("researcher", "import", "opportunity", oid, title=title, source=source)
        return self.get_opportunity(oid)

    def get_opportunity(self, oid: str) -> Opportunity:
        row = self.conn.execute(
            "SELECT * FROM pipeline_opps WHERE id=?", (oid,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no opportunity {oid}")
        return self._row_to_opp(row)

    def list_opportunities(self, status: str | None = None) -> list[Opportunity]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM pipeline_opps WHERE status=? ORDER BY updated_at DESC",
                (status,),
            )
        else:
            rows = self.conn.execute(
                "SELECT * FROM pipeline_opps ORDER BY updated_at DESC"
            )
        return [self._row_to_opp(r) for r in rows]

    def update_opportunity(self, oid: str, **fields: Any) -> Opportunity:
        allowed = {
            "fit_score",
            "fit_reasons",
            "proposal",
            "proposal_version",
            "approval_id",
            "submission_key",
            "submission_receipt",
            "project_id",
            "scope",
            "description",
            "eligibility",
            "budget_cents",
            "budget_currency",
            "deadline",
            "skills",
            "client_signals",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"cannot update: {sorted(unknown)}")
        cols = []
        vals: list[Any] = []
        for k, v in fields.items():
            if k in ("fit_reasons", "skills"):
                cols.append(f"{'skills_json' if k == 'skills' else 'fit_reasons'}=?")
                vals.append(json.dumps(v))
            elif k == "proposal":
                cols.append("proposal_json=?")
                vals.append(json.dumps(v) if v is not None else None)
            else:
                cols.append(f"{k}=?")
                vals.append(v)
        cols.append("updated_at=?")
        vals.append(time.time())
        vals.append(oid)
        self.conn.execute(
            f"UPDATE pipeline_opps SET {', '.join(cols)} WHERE id=?", vals
        )
        self.conn.commit()
        return self.get_opportunity(oid)

    def advance(self, oid: str, to: OppStatus | str) -> Opportunity:
        opp = self.get_opportunity(oid)
        current = OppStatus(opp.status)
        target = OppStatus(to) if isinstance(to, str) else to
        allowed = TRANSITIONS.get(current, frozenset())
        if target not in allowed:
            raise IllegalTransition(f"{current.value} → {target.value} is not allowed")
        self.conn.execute(
            "UPDATE pipeline_opps SET status=?, updated_at=? WHERE id=?",
            (target.value, time.time(), oid),
        )
        self.conn.commit()
        self.audit("system", "advance", "opportunity", oid, from_=current.value, to=target.value)
        return self.get_opportunity(oid)

    # ------------------------------------------------------------- approvals

    def create_approval(
        self,
        *,
        kind: str,
        entity_type: str,
        entity_id: str,
        summary: str,
        body: str = "",
        amount_cents: int | None = None,
    ) -> dict:
        aid = f"apr_{uuid.uuid4().hex[:12]}"
        now = time.time()
        self.conn.execute(
            "INSERT INTO approvals (id, kind, entity_type, entity_id, summary, body,"
            " amount_cents, status, created_at) VALUES (?,?,?,?,?,?,?,'pending',?)",
            (aid, kind, entity_type, entity_id, summary, body, amount_cents, now),
        )
        self.conn.commit()
        self.audit("system", "approval_created", "approval", aid, kind=kind)
        return self.get_approval(aid)

    def get_approval(self, aid: str) -> dict:
        row = self.conn.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
        if row is None:
            raise KeyError(aid)
        return dict(row)

    def pending_approvals(self) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM approvals WHERE status='pending' ORDER BY created_at"
            )
        ]

    def decide_approval(
        self, aid: str, *, approved: bool, decided_by: str = "owner", reason: str = ""
    ) -> dict:
        status = "approved" if approved else "rejected"
        self.conn.execute(
            "UPDATE approvals SET status=?, decided_by=?, reason=?, decided_at=? WHERE id=?",
            (status, decided_by, reason, time.time(), aid),
        )
        self.conn.commit()
        self.audit(decided_by, f"approval_{status}", "approval", aid, reason=reason)
        return self.get_approval(aid)

    # ----------------------------------------------------------- agent runs

    def active_agent_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM agent_runs WHERE status IN ('running','queued')"
        ).fetchone()
        return int(row["c"])

    def start_agent(
        self,
        *,
        role: str,
        name: str,
        hypothesis: str = "",
        deliverable: str = "",
        parent_id: str | None = None,
        token_budget: int = 50_000,
        cost_budget_cents: int = 200,
        time_budget_sec: int = 600,
        max_retries: int = 2,
        expires_at: float | None = None,
    ) -> dict:
        rid = f"run_{uuid.uuid4().hex[:12]}"
        now = time.time()
        self.conn.execute(
            "INSERT INTO agent_runs ("
            " id, role, name, status, hypothesis, deliverable, parent_id,"
            " token_budget, cost_budget_cents, time_budget_sec, max_retries,"
            " expires_at, started_at"
            ") VALUES (?,?,?,'running',?,?,?,?,?,?,?,?,?)",
            (
                rid,
                role,
                name,
                hypothesis,
                deliverable,
                parent_id,
                token_budget,
                cost_budget_cents,
                time_budget_sec,
                max_retries,
                expires_at,
                now,
            ),
        )
        self.conn.commit()
        self.audit("coordinator", "agent_start", "agent_run", rid, role=role, name=name)
        return self.get_agent_run(rid)

    def get_agent_run(self, rid: str) -> dict:
        row = self.conn.execute("SELECT * FROM agent_runs WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise KeyError(rid)
        d = dict(row)
        if d.get("output_json"):
            d["output"] = json.loads(d["output_json"])
        return d

    def list_agent_runs(self, active_only: bool = False) -> list[dict]:
        if active_only:
            rows = self.conn.execute(
                "SELECT * FROM agent_runs WHERE status IN ('running','queued') ORDER BY started_at DESC"
            )
        else:
            rows = self.conn.execute("SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT 100")
        return [dict(r) for r in rows]

    def finish_agent(
        self,
        rid: str,
        *,
        status: str = "completed",
        output: dict | None = None,
        error: str = "",
        cost_cents: int = 0,
        tokens_used: int = 0,
    ) -> dict:
        self.conn.execute(
            "UPDATE agent_runs SET status=?, output_json=?, error=?,"
            " cost_cents=cost_cents+?, tokens_used=tokens_used+?, finished_at=? WHERE id=?",
            (
                status,
                json.dumps(output or {}),
                error,
                cost_cents,
                tokens_used,
                time.time(),
                rid,
            ),
        )
        self.conn.commit()
        return self.get_agent_run(rid)

    def cancel_agent(self, rid: str, reason: str = "cancelled") -> dict:
        return self.finish_agent(rid, status="cancelled", error=reason)

    def total_agent_cost_cents(self) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_cents),0) s FROM agent_runs"
        ).fetchone()
        return int(row["s"])

    # ---------------------------------------------------------------- tasks

    def enqueue_task(
        self,
        *,
        kind: str,
        idempotency_key: str,
        payload: dict | None = None,
        agent_run_id: str | None = None,
        priority: int = 100,
    ) -> dict | None:
        """Enqueue a task. Returns None if the idempotency key already exists."""
        tid = f"tsk_{uuid.uuid4().hex[:12]}"
        now = time.time()
        try:
            self.conn.execute(
                "INSERT INTO tasks (id, agent_run_id, kind, payload_json, status,"
                " idempotency_key, priority, created_at, updated_at)"
                " VALUES (?,?,?,?,'queued',?,?,?,?)",
                (
                    tid,
                    agent_run_id,
                    kind,
                    json.dumps(payload or {}),
                    idempotency_key,
                    priority,
                    now,
                    now,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            existing = self.conn.execute(
                "SELECT * FROM tasks WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            return dict(existing) if existing else None
        return self.get_task(tid)

    def get_task(self, tid: str) -> dict:
        row = self.conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        if row is None:
            raise KeyError(tid)
        d = dict(row)
        d["payload"] = json.loads(d["payload_json"] or "{}")
        if d.get("result_json"):
            d["result"] = json.loads(d["result_json"])
        return d

    def claim_task(self, tid: str) -> dict:
        self.conn.execute(
            "UPDATE tasks SET status='running', updated_at=? WHERE id=? AND status='queued'",
            (time.time(), tid),
        )
        self.conn.commit()
        return self.get_task(tid)

    def complete_task(self, tid: str, result: dict | None = None, status: str = "completed") -> dict:
        self.conn.execute(
            "UPDATE tasks SET status=?, result_json=?, updated_at=?, completed_at=? WHERE id=?",
            (status, json.dumps(result or {}), time.time(), time.time(), tid),
        )
        self.conn.commit()
        return self.get_task(tid)

    def queued_tasks(self) -> list[dict]:
        return [
            self.get_task(r["id"])
            for r in self.conn.execute(
                "SELECT id FROM tasks WHERE status='queued' ORDER BY priority, created_at"
            )
        ]

    # ------------------------------------------------------------- projects

    def create_project(
        self,
        *,
        opportunity_id: str,
        brief: str,
        acceptance: list[str],
        workspace_path: str,
    ) -> dict:
        pid = f"prj_{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            "INSERT INTO projects (id, opportunity_id, brief, acceptance_json,"
            " workspace_path, status, created_at) VALUES (?,?,?,?,?,'active',?)",
            (pid, opportunity_id, brief, json.dumps(acceptance), workspace_path, time.time()),
        )
        self.conn.commit()
        self.conn.execute(
            "UPDATE pipeline_opps SET project_id=?, updated_at=? WHERE id=?",
            (pid, time.time(), opportunity_id),
        )
        self.conn.commit()
        self.audit("planner", "project_created", "project", pid, opportunity_id=opportunity_id)
        return self.get_project(pid)

    def get_project(self, pid: str) -> dict:
        row = self.conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        if row is None:
            raise KeyError(pid)
        d = dict(row)
        d["acceptance"] = json.loads(d["acceptance_json"] or "[]")
        return d

    def list_projects(self) -> list[dict]:
        return [
            self.get_project(r["id"])
            for r in self.conn.execute("SELECT id FROM projects ORDER BY created_at DESC")
        ]

    def update_project(self, pid: str, **fields: Any) -> dict:
        allowed = {"status", "preview_path", "delivered_at", "accepted_at", "brief"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"cannot update: {sorted(unknown)}")
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(
            f"UPDATE projects SET {sets} WHERE id=?", (*fields.values(), pid)
        )
        self.conn.commit()
        return self.get_project(pid)

    def add_milestone(self, project_id: str, title: str, acceptance: str, sort_order: int = 0) -> dict:
        mid = f"ms_{uuid.uuid4().hex[:10]}"
        self.conn.execute(
            "INSERT INTO milestones (id, project_id, title, acceptance, sort_order) VALUES (?,?,?,?,?)",
            (mid, project_id, title, acceptance, sort_order),
        )
        self.conn.commit()
        return dict(
            self.conn.execute("SELECT * FROM milestones WHERE id=?", (mid,)).fetchone()
        )

    def milestones_for(self, project_id: str) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM milestones WHERE project_id=? ORDER BY sort_order",
                (project_id,),
            )
        ]

    # ---------------------------------------------------------- experiments

    def create_experiment(
        self,
        *,
        hypothesis: str,
        metric: str,
        bottleneck: str,
        expires_at: float,
        co_agent_id: str | None = None,
    ) -> dict:
        eid = f"exp_{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            "INSERT INTO experiments (id, hypothesis, metric, bottleneck, co_agent_id,"
            " created_at, expires_at) VALUES (?,?,?,?,?,?,?)",
            (eid, hypothesis, metric, bottleneck, co_agent_id, time.time(), expires_at),
        )
        self.conn.commit()
        return self.get_experiment(eid)

    def get_experiment(self, eid: str) -> dict:
        row = self.conn.execute("SELECT * FROM experiments WHERE id=?", (eid,)).fetchone()
        if row is None:
            raise KeyError(eid)
        return dict(row)

    def active_experiments(self) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM experiments WHERE status='active' ORDER BY created_at DESC"
            )
        ]

    def close_experiment(self, eid: str, result: dict) -> dict:
        self.conn.execute(
            "UPDATE experiments SET status='closed', result_json=?, closed_at=? WHERE id=?",
            (json.dumps(result), time.time(), eid),
        )
        self.conn.commit()
        return self.get_experiment(eid)

    def record_review_cycle(self, bottleneck: str, findings: dict, experiment_id: str | None) -> dict:
        rid = f"rev_{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            "INSERT INTO review_cycles (id, bottleneck, findings_json, experiment_id, created_at)"
            " VALUES (?,?,?,?,?)",
            (rid, bottleneck, json.dumps(findings), experiment_id, time.time()),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM review_cycles WHERE id=?", (rid,)).fetchone())

    def latest_review(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM review_cycles ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["findings"] = json.loads(d["findings_json"])
        return d

    # --------------------------------------------------- standing auth

    def upsert_standing_auth(
        self,
        *,
        platform: str,
        service: str,
        max_price_cents: int,
        daily_volume: int,
        daily_spend_cents: int,
        enabled: bool = True,
    ) -> dict:
        aid = f"auth_{platform}_{service}"
        self.conn.execute(
            "INSERT INTO standing_auth (id, platform, service, max_price_cents,"
            " daily_volume, daily_spend_cents, enabled, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET max_price_cents=excluded.max_price_cents,"
            " daily_volume=excluded.daily_volume, daily_spend_cents=excluded.daily_spend_cents,"
            " enabled=excluded.enabled",
            (
                aid,
                platform,
                service,
                max_price_cents,
                daily_volume,
                daily_spend_cents,
                1 if enabled else 0,
                time.time(),
            ),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM standing_auth WHERE id=?", (aid,)).fetchone())

    def list_standing_auth(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM standing_auth")]

    def auth_usage_today(self, platform: str, service: str) -> dict:
        day = time.strftime("%Y-%m-%d", time.gmtime())
        row = self.conn.execute(
            "SELECT * FROM auth_usage WHERE day=? AND platform=? AND service=?",
            (day, platform, service),
        ).fetchone()
        if row:
            return dict(row)
        return {"day": day, "platform": platform, "service": service, "volume": 0, "spend_cents": 0}

    def record_auth_usage(self, platform: str, service: str, spend_cents: int = 0) -> None:
        day = time.strftime("%Y-%m-%d", time.gmtime())
        self.conn.execute(
            "INSERT INTO auth_usage (day, platform, service, volume, spend_cents)"
            " VALUES (?,?,?,1,?)"
            " ON CONFLICT(day, platform, service) DO UPDATE SET"
            " volume=volume+1, spend_cents=spend_cents+excluded.spend_cents",
            (day, platform, service, spend_cents),
        )
        self.conn.commit()

    # ----------------------------------------------------------- invoices

    def record_firm_invoice(
        self,
        *,
        project_id: str,
        provider: str,
        provider_ref: str,
        amount_cents: int,
        currency: str = "usd",
        lifecycle: str = "issued",
        url: str | None = None,
        simulated: bool = False,
        original_currency: str | None = None,
        fx_rate: float | None = None,
        fees_cents: int = 0,
    ) -> dict:
        iid = f"finv_{uuid.uuid4().hex[:12]}"
        try:
            self.conn.execute(
                "INSERT INTO firm_invoices ("
                " id, project_id, provider, provider_ref, amount_cents, currency,"
                " original_currency, fx_rate, fees_cents, lifecycle, url, simulated, created_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    iid,
                    project_id,
                    provider,
                    provider_ref,
                    amount_cents,
                    currency,
                    original_currency,
                    fx_rate,
                    fees_cents,
                    lifecycle,
                    url,
                    1 if simulated else 0,
                    time.time(),
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            row = self.conn.execute(
                "SELECT * FROM firm_invoices WHERE provider=? AND provider_ref=?",
                (provider, provider_ref),
            ).fetchone()
            return dict(row)
        self.audit("finance", "invoice_issued", "invoice", iid, amount_cents=amount_cents)
        return dict(self.conn.execute("SELECT * FROM firm_invoices WHERE id=?", (iid,)).fetchone())

    def set_invoice_lifecycle(self, iid: str, lifecycle: str) -> dict:
        self.conn.execute(
            "UPDATE firm_invoices SET lifecycle=? WHERE id=?", (lifecycle, iid)
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM firm_invoices WHERE id=?", (iid,)).fetchone())

    def confirm_payment(
        self,
        invoice_id: str,
        provider_event_id: str,
        amount_cents: int,
        currency: str = "usd",
    ) -> bool:
        """Idempotent payment confirmation. Returns False if already applied."""
        pid = f"fpay_{uuid.uuid4().hex[:12]}"
        try:
            self.conn.execute(
                "INSERT INTO firm_payments (id, invoice_id, provider_event_id,"
                " amount_cents, currency, confirmed_at) VALUES (?,?,?,?,?,?)",
                (pid, invoice_id, provider_event_id, amount_cents, currency, time.time()),
            )
        except sqlite3.IntegrityError:
            return False
        self.conn.execute(
            "UPDATE firm_invoices SET lifecycle='settled' WHERE id=?", (invoice_id,)
        )
        self.conn.commit()
        self.audit(
            "finance",
            "payment_settled",
            "payment",
            pid,
            invoice_id=invoice_id,
            amount_cents=amount_cents,
            provider_event_id=provider_event_id,
        )
        return True

    def gross_revenue_cents(self) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) s FROM firm_payments"
        ).fetchone()
        return int(row["s"])

    def invoiced_cents(self) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) s FROM firm_invoices"
            " WHERE lifecycle NOT IN ('void','settled')"
        ).fetchone()
        return int(row["s"])

    def list_invoices(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM firm_invoices ORDER BY created_at DESC")]

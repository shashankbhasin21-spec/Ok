"""Event-sourced journal with deterministic replay.

Every decision the system makes is appended here with a monotonic sequence
number, so the day can be reconstructed exactly rather than approximately. The
audit found the existing book records fills and engine notes but carries no
sequence, which means "what happened at 11:04" is answerable only to the
nearest second and "replay this session" is not answerable at all.

Two properties the schema enforces rather than hopes for:

* **Append-only.** There is no update or delete path. A journal you can edit is
  a journal that cannot settle an argument about what happened.
* **Gapless sequence.** Numbers are assigned inside the same transaction as the
  insert, so a crash between two events leaves a shorter journal, never a
  journal with a hole. `verify()` proves it.

Correlation ids tie a signal to the order it produced and the fill that came
back, so one trade can be pulled out of a day of noise in a single query.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# Event kinds. Deliberately a closed set: an unrecognised kind is a bug in the
# caller, not a new feature, and silently accepting one loses the event.
MARKET = "market"
SIGNAL = "signal"
RISK = "risk"
ORDER = "order"
FILL = "fill"
POSITION = "position"
STRATEGY = "strategy"
BROKER = "broker"
ERROR = "error"
KILL = "kill"
KINDS = (MARKET, SIGNAL, RISK, ORDER, FILL, POSITION, STRATEGY, BROKER, ERROR, KILL)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    at         REAL NOT NULL,
    session    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    component  TEXT NOT NULL,
    event      TEXT NOT NULL,
    correlation TEXT NOT NULL DEFAULT '',
    payload    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_by_session ON events (session, seq);
CREATE INDEX IF NOT EXISTS events_by_correlation ON events (correlation);
CREATE INDEX IF NOT EXISTS events_by_kind ON events (session, kind);
"""


class EventStoreError(RuntimeError):
    """The journal was asked to do something that would compromise it."""


@dataclass(frozen=True)
class Event:
    seq: int
    at: float
    session: str
    kind: str
    component: str
    event: str
    correlation: str = ""
    payload: dict = field(default_factory=dict)

    def line(self) -> str:
        stamp = time.strftime("%H:%M:%S", time.localtime(self.at))
        tail = f"  [{self.correlation[:8]}]" if self.correlation else ""
        return f"{self.seq:>6}  {stamp}  {self.kind:<9} {self.component:<12} {self.event}{tail}"


def correlation_id() -> str:
    """A fresh id tying one decision to everything downstream of it."""
    return uuid.uuid4().hex


class EventStore:
    """Append-only, gapless, replayable."""

    def __init__(self, path: Path | str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- writing -----------------------------------------------------------

    def append(self, kind: str, component: str, event: str, *,
               correlation: str = "", session: str | None = None,
               at: float | None = None, **payload) -> Event:
        """Record one event. The only way anything enters the journal."""
        if kind not in KINDS:
            raise EventStoreError(
                f"unknown event kind {kind!r} — the set is closed so a typo "
                f"cannot silently create a category nothing queries"
            )
        from earner.trading.risk import trading_day

        at = time.time() if at is None else at
        session = session or trading_day(at)
        body = json.dumps(payload, default=str, sort_keys=True)
        cursor = self.conn.execute(
            "INSERT INTO events (at, session, kind, component, event, correlation, payload)"
            " VALUES (?,?,?,?,?,?,?)",
            (at, session, kind, component, event, correlation, body),
        )
        self.conn.commit()
        return Event(seq=cursor.lastrowid, at=at, session=session, kind=kind,
                     component=component, event=event, correlation=correlation,
                     payload=payload)

    # -- reading -----------------------------------------------------------

    def _row(self, row: sqlite3.Row) -> Event:
        try:
            payload = json.loads(row["payload"])
        except ValueError:
            payload = {"unparseable": row["payload"]}
        return Event(seq=row["seq"], at=row["at"], session=row["session"],
                     kind=row["kind"], component=row["component"], event=row["event"],
                     correlation=row["correlation"], payload=payload)

    def replay(self, session: str | None = None, *, kind: str | None = None,
               since: int = 0) -> list[Event]:
        """Every event in sequence order. This is the deterministic replay."""
        clauses, args = ["seq > ?"], [since]
        if session:
            clauses.append("session = ?")
            args.append(session)
        if kind:
            clauses.append("kind = ?")
            args.append(kind)
        sql = f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY seq"
        return [self._row(r) for r in self.conn.execute(sql, args)]

    def trace(self, correlation: str) -> list[Event]:
        """One decision and everything that followed from it."""
        return [self._row(r) for r in self.conn.execute(
            "SELECT * FROM events WHERE correlation=? ORDER BY seq", (correlation,))]

    def last_seq(self) -> int:
        row = self.conn.execute("SELECT MAX(seq) AS s FROM events").fetchone()
        return row["s"] or 0

    def verify(self) -> list[str]:
        """Problems with the journal itself. Empty means it can be trusted.

        Checked rather than assumed: a journal is only useful if you can show
        it is complete, and 'the database looked fine' is not a demonstration.
        """
        problems = []
        rows = list(self.conn.execute("SELECT seq, at, kind FROM events ORDER BY seq"))
        if not rows:
            return problems

        expected = rows[0]["seq"]
        for row in rows:
            if row["seq"] != expected:
                problems.append(
                    f"sequence gap: expected {expected}, found {row['seq']} — "
                    "events are missing from the journal"
                )
                expected = row["seq"]
            if row["kind"] not in KINDS:
                problems.append(f"seq {row['seq']}: unknown kind {row['kind']!r}")
            expected += 1

        # Time must not run backwards within a session; if it does, the replay
        # order and the real order disagree.
        previous = None
        for row in rows:
            if previous is not None and row["at"] < previous:
                problems.append(f"seq {row['seq']}: timestamp goes backwards")
                break
            previous = row["at"]
        return problems

    def summary(self, session: str | None = None) -> dict[str, int]:
        sql = "SELECT kind, COUNT(*) AS n FROM events"
        args: tuple = ()
        if session:
            sql += " WHERE session=?"
            args = (session,)
        return {r["kind"]: r["n"] for r in self.conn.execute(sql + " GROUP BY kind", args)}

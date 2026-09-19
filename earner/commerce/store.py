"""Persistence for Grey Quantum commerce catalog, orders, ads, teams, ideas."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class CommerceStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                slug TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                niche TEXT NOT NULL,
                description TEXT NOT NULL,
                source_market TEXT NOT NULL,
                source_url TEXT,
                source_price_cents INTEGER NOT NULL,
                sell_price_cents INTEGER NOT NULL,
                currency TEXT NOT NULL DEFAULT 'USD',
                shipping_estimate_cents INTEGER NOT NULL DEFAULT 0,
                margin_cents INTEGER NOT NULL,
                status TEXT NOT NULL,
                page_html_path TEXT,
                evidence_note TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                simulated INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS loops (
                id TEXT PRIMARY KEY,
                niche TEXT NOT NULL,
                hypothesis TEXT NOT NULL,
                source_price_cents INTEGER NOT NULL,
                sell_price_cents INTEGER NOT NULL,
                estimated_margin_cents INTEGER NOT NULL,
                risk_note TEXT NOT NULL,
                status TEXT NOT NULL,
                product_id TEXT,
                found_by TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ad_drafts (
                id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                headline TEXT NOT NULL,
                body TEXT NOT NULL,
                cta TEXT NOT NULL,
                status TEXT NOT NULL,
                spend_cap_cents INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                published_at REAL
            );
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL,
                customer_email TEXT NOT NULL,
                customer_name TEXT,
                ship_country TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                currency TEXT NOT NULL,
                status TEXT NOT NULL,
                provider TEXT,
                provider_ref TEXT,
                simulated INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                paid_at REAL
            );
            CREATE TABLE IF NOT EXISTS agent_teams (
                id TEXT PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                mission TEXT NOT NULL,
                headcount INTEGER NOT NULL,
                status TEXT NOT NULL,
                last_run_at REAL,
                last_output_json TEXT
            );
            CREATE TABLE IF NOT EXISTS discussion_ideas (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                niche TEXT,
                status TEXT NOT NULL,
                raised_by TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ceo_directives (
                id TEXT PRIMARY KEY,
                directive TEXT NOT NULL,
                priority TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                closed_at REAL
            );
            CREATE TABLE IF NOT EXISTS commerce_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outreach_drafts (
                id TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                audience TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )
        self.conn.commit()

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO commerce_meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM commerce_meta WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default

    # ── products ──────────────────────────────────────────────────────────
    def upsert_product(self, **fields: Any) -> dict:
        now = time.time()
        pid = fields.get("id") or _uid("prd")
        existing = None
        if fields.get("slug"):
            existing = self.conn.execute(
                "SELECT id FROM products WHERE slug=?", (fields["slug"],)
            ).fetchone()
        if existing:
            pid = existing["id"]
            cols = [
                "title", "niche", "description", "source_market", "source_url",
                "source_price_cents", "sell_price_cents", "currency",
                "shipping_estimate_cents", "margin_cents", "status",
                "page_html_path", "evidence_note", "simulated",
            ]
            sets = ", ".join(f"{c}=?" for c in cols if c in fields)
            vals = [fields[c] for c in cols if c in fields]
            vals.extend([now, pid])
            self.conn.execute(
                f"UPDATE products SET {sets}, updated_at=? WHERE id=?", vals
            )
        else:
            self.conn.execute(
                """
                INSERT INTO products(
                    id, slug, title, niche, description, source_market, source_url,
                    source_price_cents, sell_price_cents, currency, shipping_estimate_cents,
                    margin_cents, status, page_html_path, evidence_note, created_at,
                    updated_at, simulated
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    pid,
                    fields["slug"],
                    fields["title"],
                    fields["niche"],
                    fields["description"],
                    fields.get("source_market", "GLOBAL"),
                    fields.get("source_url"),
                    int(fields["source_price_cents"]),
                    int(fields["sell_price_cents"]),
                    fields.get("currency", "USD"),
                    int(fields.get("shipping_estimate_cents", 0)),
                    int(fields["margin_cents"]),
                    fields.get("status", "draft"),
                    fields.get("page_html_path"),
                    fields.get("evidence_note", ""),
                    now,
                    now,
                    1 if fields.get("simulated") else 0,
                ),
            )
        self.conn.commit()
        return self.get_product(pid)

    def get_product(self, product_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM products WHERE id=?", (product_id,)
        ).fetchone()
        if not row:
            raise KeyError(product_id)
        return dict(row)

    def get_product_by_slug(self, slug: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM products WHERE slug=?", (slug,)
        ).fetchone()
        if not row:
            raise KeyError(slug)
        return dict(row)

    def list_products(self, *, published_only: bool = False, include_simulated: bool = False) -> list[dict]:
        q = "SELECT * FROM products WHERE 1=1"
        if published_only:
            q += " AND status='published'"
        if not include_simulated:
            q += " AND simulated=0"
        q += " ORDER BY updated_at DESC"
        return [dict(r) for r in self.conn.execute(q).fetchall()]

    # ── loops ─────────────────────────────────────────────────────────────
    def add_loop(self, **fields: Any) -> dict:
        lid = _uid("loop")
        self.conn.execute(
            """
            INSERT INTO loops(
                id, niche, hypothesis, source_price_cents, sell_price_cents,
                estimated_margin_cents, risk_note, status, product_id, found_by, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                lid,
                fields["niche"],
                fields["hypothesis"],
                int(fields["source_price_cents"]),
                int(fields["sell_price_cents"]),
                int(fields["estimated_margin_cents"]),
                fields.get("risk_note", ""),
                fields.get("status", "candidate"),
                fields.get("product_id"),
                fields.get("found_by", "loophole_team"),
                time.time(),
            ),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM loops WHERE id=?", (lid,)).fetchone())

    def list_loops(self, limit: int = 50) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM loops ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        ]

    def attach_loop_product(self, loop_id: str, product_id: str) -> None:
        self.conn.execute(
            "UPDATE loops SET product_id=?, status='implemented' WHERE id=?",
            (product_id, loop_id),
        )
        self.conn.commit()

    # ── ads ───────────────────────────────────────────────────────────────
    def add_ad_draft(self, **fields: Any) -> dict:
        aid = _uid("ad")
        self.conn.execute(
            """
            INSERT INTO ad_drafts(
                id, product_id, channel, headline, body, cta, status,
                spend_cap_cents, created_at, published_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                aid,
                fields["product_id"],
                fields["channel"],
                fields["headline"],
                fields["body"],
                fields["cta"],
                fields.get("status", "draft"),
                int(fields.get("spend_cap_cents", 0)),
                time.time(),
                None,
            ),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM ad_drafts WHERE id=?", (aid,)).fetchone())

    def list_ad_drafts(self, limit: int = 50) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM ad_drafts ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        ]

    # ── orders ────────────────────────────────────────────────────────────
    def create_order(self, **fields: Any) -> dict:
        oid = _uid("ord")
        self.conn.execute(
            """
            INSERT INTO orders(
                id, product_id, customer_email, customer_name, ship_country,
                amount_cents, currency, status, provider, provider_ref,
                simulated, created_at, paid_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                oid,
                fields["product_id"],
                fields["customer_email"],
                fields.get("customer_name"),
                fields["ship_country"],
                int(fields["amount_cents"]),
                fields.get("currency", "USD"),
                fields.get("status", "pending_payment"),
                fields.get("provider"),
                fields.get("provider_ref"),
                1 if fields.get("simulated") else 0,
                time.time(),
                None,
            ),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())

    def mark_order_paid(self, order_id: str, *, provider: str, provider_ref: str) -> dict:
        if not provider or not provider_ref:
            raise ValueError("paid orders require provider + provider_ref evidence")
        self.conn.execute(
            """
            UPDATE orders SET status='paid', provider=?, provider_ref=?, paid_at=?
            WHERE id=? AND simulated=0
            """,
            (provider, provider_ref, time.time(), order_id),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            raise KeyError(order_id)
        return dict(row)

    def list_orders(self, *, include_simulated: bool = False) -> list[dict]:
        q = "SELECT * FROM orders"
        if not include_simulated:
            q += " WHERE simulated=0"
        q += " ORDER BY created_at DESC"
        return [dict(r) for r in self.conn.execute(q).fetchall()]

    def settled_sales_cents(self) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) AS s FROM orders "
            "WHERE status='paid' AND simulated=0"
        ).fetchone()
        return int(row["s"])

    def month_settled_sales_cents(self, month_prefix: str | None = None) -> int:
        # SQLite stores unix time; filter by paid_at calendar month UTC via python if needed.
        # For simplicity: sum all paid in last 30 days when month_prefix omitted.
        if month_prefix:
            # month_prefix like 2026-09 — approximate with string compare on paid_at via strftime
            row = self.conn.execute(
                """
                SELECT COALESCE(SUM(amount_cents),0) AS s FROM orders
                WHERE status='paid' AND simulated=0
                  AND strftime('%Y-%m', paid_at, 'unixepoch')=?
                """,
                (month_prefix,),
            ).fetchone()
        else:
            cutoff = time.time() - 30 * 86400
            row = self.conn.execute(
                """
                SELECT COALESCE(SUM(amount_cents),0) AS s FROM orders
                WHERE status='paid' AND simulated=0 AND paid_at >= ?
                """,
                (cutoff,),
            ).fetchone()
        return int(row["s"])

    # ── teams / discussion / CEO ──────────────────────────────────────────
    def upsert_team(self, **fields: Any) -> dict:
        existing = self.conn.execute(
            "SELECT id FROM agent_teams WHERE code=?", (fields["code"],)
        ).fetchone()
        if existing:
            self.conn.execute(
                """
                UPDATE agent_teams SET name=?, mission=?, headcount=?, status=?,
                last_run_at=?, last_output_json=? WHERE code=?
                """,
                (
                    fields["name"],
                    fields["mission"],
                    int(fields["headcount"]),
                    fields.get("status", "ready"),
                    fields.get("last_run_at"),
                    fields.get("last_output_json"),
                    fields["code"],
                ),
            )
            tid = existing["id"]
        else:
            tid = _uid("team")
            self.conn.execute(
                """
                INSERT INTO agent_teams(
                    id, code, name, mission, headcount, status, last_run_at, last_output_json
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    tid,
                    fields["code"],
                    fields["name"],
                    fields["mission"],
                    int(fields["headcount"]),
                    fields.get("status", "ready"),
                    fields.get("last_run_at"),
                    fields.get("last_output_json"),
                ),
            )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM agent_teams WHERE id=?", (tid,)).fetchone())

    def list_teams(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM agent_teams ORDER BY code"
        ).fetchall()]

    def add_idea(self, **fields: Any) -> dict:
        iid = _uid("idea")
        self.conn.execute(
            """
            INSERT INTO discussion_ideas(id, title, body, niche, status, raised_by, created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                iid,
                fields["title"],
                fields["body"],
                fields.get("niche"),
                fields.get("status", "open"),
                fields.get("raised_by", "discussion_room"),
                time.time(),
            ),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM discussion_ideas WHERE id=?", (iid,)).fetchone())

    def list_ideas(self, limit: int = 40) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM discussion_ideas ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        ]

    def add_directive(self, directive: str, priority: str = "normal") -> dict:
        did = _uid("dir")
        self.conn.execute(
            """
            INSERT INTO ceo_directives(id, directive, priority, status, created_at, closed_at)
            VALUES (?,?,?,?,?,?)
            """,
            (did, directive, priority, "open", time.time(), None),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM ceo_directives WHERE id=?", (did,)).fetchone())

    def list_directives(self, limit: int = 30) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM ceo_directives ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        ]

    def add_outreach_draft(self, **fields: Any) -> dict:
        oid = _uid("out")
        self.conn.execute(
            """
            INSERT INTO outreach_drafts(id, channel, audience, message, status, created_at)
            VALUES (?,?,?,?,?,?)
            """,
            (
                oid,
                fields["channel"],
                fields["audience"],
                fields["message"],
                fields.get("status", "draft"),
                time.time(),
            ),
        )
        self.conn.commit()
        return dict(self.conn.execute("SELECT * FROM outreach_drafts WHERE id=?", (oid,)).fetchone())

    def list_outreach_drafts(self, limit: int = 40) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM outreach_drafts ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        ]

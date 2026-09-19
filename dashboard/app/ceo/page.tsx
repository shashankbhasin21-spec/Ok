"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

type Team = {
  code: string;
  name: string;
  mission: string;
  headcount: number;
  status: string;
  last_run_at: number | null;
};

type Snapshot = {
  ceo: string;
  title: string;
  company: string;
  targets: Record<string, number | string>;
  settled_sales_usd: number;
  month_settled_sales_usd: number;
  catalog: Record<string, number>;
  blocker: string;
  teams: Team[];
  loops: Array<Record<string, unknown>>;
  ads: Array<Record<string, unknown>>;
  ideas: Array<Record<string, unknown>>;
  directives: Array<Record<string, unknown>>;
  outreach_drafts: Array<Record<string, unknown>>;
  orders: Array<Record<string, unknown>>;
  payment_note: string;
};

async function api(path: string, init?: RequestInit) {
  const res = await fetch(`/api/proxy${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function ownerHeaders(): HeadersInit {
  const secret =
    typeof window !== "undefined"
      ? sessionStorage.getItem("gq_owner_secret") || ""
      : "";
  return secret ? { "X-Owner-Secret": secret } : {};
}

export default function CeoPage() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const [directive, setDirective] = useState("");

  const refresh = useCallback(async () => {
    try {
      const d = await api("/commerce");
      setData(d);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 12000);
    return () => clearInterval(t);
  }, [refresh]);

  function ensureSecret() {
    let secret = sessionStorage.getItem("gq_owner_secret") || "";
    if (!secret) {
      secret = prompt("Owner secret (FIRM_OWNER_SECRET)") || "";
      if (secret) sessionStorage.setItem("gq_owner_secret", secret);
    }
    return secret;
  }

  async function runDay(publish: boolean) {
    setBusy(publish ? "publish" : "run");
    try {
      ensureSecret();
      await api("/commerce/run", {
        method: "POST",
        headers: ownerHeaders(),
        body: JSON.stringify({ publish, outreach: true }),
      });
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  async function issueDirective(e: React.FormEvent) {
    e.preventDefault();
    if (!directive.trim()) return;
    setBusy("dir");
    try {
      ensureSecret();
      await api("/commerce/directive", {
        method: "POST",
        headers: ownerHeaders(),
        body: JSON.stringify({ directive, priority: "high" }),
      });
      setDirective("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="gq gq-pad ceo">
      <header className="gq-nav">
        <Link href="/" className="gq-logo">
          Grey Quantum
        </Link>
        <nav>
          <Link href="/">Store</Link>
          <Link href="/ops">Firm Ops</Link>
        </nav>
      </header>

      <section className="ceo-hero">
        <p className="gq-brand">Quantum Brain CEO</p>
        <h1>{data?.ceo || "Anestasis Grey"}</h1>
        <p className="gq-lede">
          Full command of Grey Quantum commerce teams — production finder, page developer, daily ads,
          5-agent loophole squad, discussion room, and optional outreach. Targets are not cash.
        </p>
        <div className="gq-cta">
          <button className="gq-btn primary" type="button" disabled={!!busy} onClick={() => runDay(true)}>
            {busy === "publish" ? "Running…" : "Run day + publish"}
          </button>
          <button className="gq-btn" type="button" disabled={!!busy} onClick={() => runDay(false)}>
            {busy === "run" ? "Running…" : "Run day (draft only)"}
          </button>
        </div>
      </section>

      {error && <p className="gq-error">{error}</p>}

      <section className="ceo-metrics">
        <div>
          <span className="label">Settled</span>
          <strong>${(data?.settled_sales_usd ?? 0).toLocaleString()}</strong>
        </div>
        <div>
          <span className="label">Month settled</span>
          <strong>${(data?.month_settled_sales_usd ?? 0).toLocaleString()}</strong>
        </div>
        <div>
          <span className="label">Daily / monthly targets</span>
          <strong>
            ${Number(data?.targets?.daily_sales_usd ?? 10000).toLocaleString()} / $
            {Number(data?.targets?.monthly_sales_usd ?? 500000).toLocaleString()}
          </strong>
        </div>
        <div>
          <span className="label">Catalog</span>
          <strong>
            {data?.catalog?.published ?? 0} live / {data?.catalog?.total ?? 0} total
          </strong>
        </div>
      </section>
      <p className="gq-note">{data?.blocker}</p>
      <p className="gq-note">{data?.payment_note}</p>

      <section className="ceo-block">
        <h2>Teams</h2>
        <ul className="ceo-teams">
          {(data?.teams || []).map((t) => (
            <li key={t.code}>
              <strong>
                {t.name} · {t.headcount}
              </strong>
              <span>{t.mission}</span>
              <em>
                {t.status}
                {t.last_run_at ? ` · ${new Date(t.last_run_at * 1000).toLocaleString()}` : ""}
              </em>
            </li>
          ))}
        </ul>
      </section>

      <section className="ceo-block">
        <h2>Issue directive</h2>
        <form className="gq-form row" onSubmit={issueDirective}>
          <input
            value={directive}
            onChange={(e) => setDirective(e.target.value)}
            placeholder="e.g. Prioritize fitness niche loops for India shipping this week"
          />
          <button className="gq-btn primary" type="submit" disabled={busy === "dir"}>
            Command
          </button>
        </form>
        <ul className="ceo-list">
          {(data?.directives || []).slice(0, 8).map((d) => (
            <li key={String(d.id)}>
              <strong>{String(d.priority)}</strong> — {String(d.directive)}
            </li>
          ))}
        </ul>
      </section>

      <div className="ceo-grid">
        <section className="ceo-block">
          <h2>Loops</h2>
          <ul className="ceo-list">
            {(data?.loops || []).slice(0, 8).map((l) => (
              <li key={String(l.id)}>
                {String(l.niche)}: {String(l.hypothesis).slice(0, 120)}…
              </li>
            ))}
          </ul>
        </section>
        <section className="ceo-block">
          <h2>Ad drafts</h2>
          <ul className="ceo-list">
            {(data?.ads || []).slice(0, 8).map((a) => (
              <li key={String(a.id)}>
                [{String(a.channel)}] {String(a.headline)} — {String(a.status)}
              </li>
            ))}
          </ul>
        </section>
        <section className="ceo-block">
          <h2>Discussion ideas</h2>
          <ul className="ceo-list">
            {(data?.ideas || []).slice(0, 8).map((i) => (
              <li key={String(i.id)}>
                {String(i.title)} — {String(i.body).slice(0, 100)}
              </li>
            ))}
          </ul>
        </section>
        <section className="ceo-block">
          <h2>Outreach drafts</h2>
          <ul className="ceo-list">
            {(data?.outreach_drafts || []).slice(0, 8).map((o) => (
              <li key={String(o.id)}>
                [{String(o.channel)}] {String(o.message).slice(0, 100)}…
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}

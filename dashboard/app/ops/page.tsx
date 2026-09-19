"use client";

import { useCallback, useEffect, useState } from "react";

type Dashboard = {
  paused: boolean;
  targets: { aspirational_rate_usd_per_hour: number; cash_milestone_usd: number; label: string };
  metrics: {
    by_status: Record<string, number>;
    conversion: { application_to_reply: number | null; reply_to_win: number | null };
    finance_usd: {
      gross_revenue_cents: number;
      invoiced_outstanding_cents: number;
      costs_cents: number;
      net_contribution_cents: number;
      settled_cash_cents: number;
      simulated_receipts_cents: number;
      monthly_target_cents: number;
      monthly_settled_cash_cents: number;
      monthly_target_progress: number;
      month_utc: string;
      milestone_cents: number;
      milestone_progress: number;
      observed_rate_cents_per_hour: number;
      measurement_window_hours: number;
      aspirational_rate_cents_per_hour: number;
    };
    note: string;
  };
  opportunities: Array<Record<string, unknown>>;
  approvals: Array<Record<string, unknown>>;
  agents: {
    active: Array<Record<string, unknown>>;
    recent: Array<Record<string, unknown>>;
    active_count: number;
    max_concurrent: number;
    total_cost_cents: number;
  };
  projects: Array<Record<string, unknown>>;
  invoices: Array<Record<string, unknown>>;
  experiments: Array<Record<string, unknown>>;
  bottleneck: string | null;
  latest_review: Record<string, unknown> | null;
  payouts: Record<string, unknown>;
  standing_auth: Array<Record<string, unknown>>;
  integrations: Record<string, string>;
  readiness?: Array<{ name: string; ok: boolean; detail: string }>;
  blocked?: Array<{ name: string; detail: string }>;
  live_mode?: boolean;
  provider?: string | null;
  simulated_data_policy: string;
};

function usd(cents: number | null | undefined) {
  return `$${((cents || 0) / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function pct(v: number | null | undefined) {
  if (v == null || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(0)}%`;
}

async function api(path: string, init?: RequestInit) {
  const res = await fetch(`/api/proxy${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

export default function HomePage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const [payoutSession, setPayoutSession] = useState("");
  const [ownerSecret, setOwnerSecret] = useState("");
  const [payoutForm, setPayoutForm] = useState({
    account_holder: "",
    bank_name: "",
    bank_account_number: "",
    routing_or_ifsc: "",
    upi_id: "",
    currency: "usd",
    provider: "payoneer",
  });

  const refresh = useCallback(async () => {
    try {
      const d = await api("/dashboard");
      setData(d);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 8000);
    return () => clearInterval(t);
  }, [refresh]);

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  if (!data && !error) {
    return (
      <main className="shell">
        <p className="muted">Loading firm dashboard…</p>
      </main>
    );
  }

  const fin = data?.metrics.finance_usd;
  const progress = Math.min(100, (fin?.milestone_progress || 0) * 100);

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <h1 className="brand">Firm Ops</h1>
          <p className="sub">
            Services pipeline control plane. Public store:{" "}
            <a href="/">Grey Quantum</a> · CEO: <a href="/ceo">Anestasis Grey</a>
          </p>
        </div>
        <div className="actions">
          <input
            style={{ minWidth: "12rem" }}
            type="password"
            placeholder="Owner secret"
            value={ownerSecret}
            onChange={(e) => setOwnerSecret(e.target.value)}
            autoComplete="off"
          />
          <button
            className="primary"
            disabled={!!busy}
            onClick={() =>
              run("sweep", async () => {
                await api("/live/sweep", {
                  method: "POST",
                  body: "{}",
                  headers: { "X-Owner-Secret": ownerSecret },
                });
              })
            }
          >
            {busy === "sweep" ? "Sweeping…" : "Sweep live boards"}
          </button>
          <button
            disabled={!!busy}
            onClick={() =>
              run("collect", async () => {
                await api("/live/collect", {
                  method: "POST",
                  body: "{}",
                  headers: { "X-Owner-Secret": ownerSecret },
                });
              })
            }
          >
            Collect Stripe
          </button>
          <button
            disabled={!!busy}
            onClick={() =>
              run("review", async () => {
                await api("/review", {
                  method: "POST",
                  body: "{}",
                  headers: { "X-Owner-Secret": ownerSecret },
                });
              })
            }
          >
            Hourly review
          </button>
          {data?.paused ? (
            <button className="primary" onClick={() => run("resume", async () => { await api("/resume", { method: "POST", body: "{}" }); })}>
              Resume
            </button>
          ) : (
            <button className="danger" onClick={() => run("pause", async () => { await api("/pause", { method: "POST", body: "{}" }); })}>
              Global pause
            </button>
          )}
        </div>
      </header>

      {data?.paused && <div className="banner paused">Firm is paused. External actions and agent ticks are suspended.</div>}
      {error && <div className="banner paused error">{error}</div>}
      {data && (data.blocked?.length ?? 0) > 0 && (
        <div className="banner paused">
          Live invoicing blocked until you add Stripe credentials:{" "}
          {(data.blocked || []).map((b) => b.name).join(", ")}. Settled cash stays $0 until Stripe confirms payment.
        </div>
      )}
      {data && <div className="banner">{data.simulated_data_policy}</div>}

      {data && (
        <div className="grid">
          <section className="panel span-4">
            <h2>Recorded cash (USD)</h2>
            <div className="metric">
              <span className="label">Non-sandbox receipts</span>
              <span className="value">{usd(fin?.settled_cash_cents)}</span>
              <span className="hint">{data.metrics.note}</span>
            </div>
            <div className="progress" aria-label="milestone progress">
              <span style={{ width: `${progress}%` }} />
            </div>
            <p className="muted" style={{ marginTop: "0.5rem" }}>
              Monthly goal {usd(fin?.monthly_target_cents)} · {fin?.month_utc} UTC<br />
              Collected this month: {usd(fin?.monthly_settled_cash_cents)}<br />
              Simulated receipts (excluded): {usd(fin?.simulated_receipts_cents)}<br />
              Cumulative milestone {usd(fin?.milestone_cents)} · {progress.toFixed(1)}%
            </p>
          </section>

          <section className="panel span-4">
            <h2>Unit economics</h2>
            <div className="stack">
              <div className="row"><span className="muted">Gross revenue</span><strong>{usd(fin?.gross_revenue_cents)}</strong></div>
              <div className="row"><span className="muted">Invoiced outstanding</span><strong>{usd(fin?.invoiced_outstanding_cents)}</strong></div>
              <div className="row"><span className="muted">Costs</span><strong>{usd(fin?.costs_cents)}</strong></div>
              <div className="row"><span className="muted">Net contribution</span><strong>{usd(fin?.net_contribution_cents)}</strong></div>
            </div>
          </section>

          <section className="panel span-4">
            <h2>Revenue rate</h2>
            <div className="metric">
              <span className="label">Observed / hour</span>
              <span className="value">{usd(fin?.observed_rate_cents_per_hour)}</span>
              <span className="hint">
                Window {fin?.measurement_window_hours?.toFixed(2)}h · aspirational target{" "}
                {usd(fin?.aspirational_rate_cents_per_hour)}/h (not a spawn budget)
              </span>
            </div>
          </section>

          <section className="panel span-6">
            <h2>Bottleneck & experiment</h2>
            <p>
              Current bottleneck: <span className="tag warn">{data.bottleneck || "—"}</span>
            </p>
            {data.experiments[0] ? (
              <div className="stack" style={{ marginTop: "0.75rem" }}>
                <div>
                  <div className="muted">Hypothesis</div>
                  <div>{String(data.experiments[0].hypothesis)}</div>
                </div>
                <div>
                  <div className="muted">Metric</div>
                  <div>{String(data.experiments[0].metric)}</div>
                </div>
              </div>
            ) : (
              <p className="muted">No active experiment. Run hourly review.</p>
            )}
            <div className="row" style={{ marginTop: "0.9rem" }}>
              <span className="muted">App → reply</span>
              <strong>{pct(data.metrics.conversion.application_to_reply)}</strong>
            </div>
            <div className="row">
              <span className="muted">Reply → win</span>
              <strong>{pct(data.metrics.conversion.reply_to_win)}</strong>
            </div>
          </section>

          <section className="panel span-6">
            <h2>Active agents</h2>
            <p className="muted">
              {data.agents.active_count}/{data.agents.max_concurrent} concurrent · cost{" "}
              {usd(data.agents.total_cost_cents)}
            </p>
            <table>
              <thead>
                <tr>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Cost</th>
                </tr>
              </thead>
              <tbody>
                {(data.agents.recent || []).slice(0, 8).map((a) => (
                  <tr key={String(a.id)}>
                    <td>{String(a.role)}</td>
                    <td>{String(a.status)}</td>
                    <td>{usd(Number(a.cost_cents || 0))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="panel span-8">
            <h2>Opportunity pipeline</h2>
            <table>
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Status</th>
                  <th>Fit</th>
                  <th>Budget</th>
                </tr>
              </thead>
              <tbody>
                {data.opportunities.length === 0 && (
                  <tr>
                    <td colSpan={4} className="muted">
                      No opportunities yet. Run the vertical slice or import samples.
                    </td>
                  </tr>
                )}
                {data.opportunities.map((o) => (
                  <tr key={String(o.id)}>
                    <td>
                      {String(o.title)}{" "}
                      {o.simulated ? <span className="tag sim">SAMPLE</span> : null}
                    </td>
                    <td>{String(o.status)}</td>
                    <td>{o.fit_score != null ? Number(o.fit_score).toFixed(2) : "—"}</td>
                    <td>
                      {o.budget_cents != null
                        ? `${usd(Number(o.budget_cents))} ${String(o.budget_currency || "usd").toUpperCase()}`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="panel span-4">
            <h2>Approvals</h2>
            <div className="stack">
              {data.approvals.length === 0 && <p className="muted">None waiting.</p>}
              {data.approvals.map((a) => (
                <div key={String(a.id)} style={{ borderBottom: "1px solid var(--line)", paddingBottom: "0.5rem" }}>
                  <div>{String(a.summary)}</div>
                  <div className="muted">{a.amount_cents != null ? usd(Number(a.amount_cents)) : ""}</div>
                  <div className="actions" style={{ marginTop: "0.4rem" }}>
                    <button
                      className="primary"
                      onClick={() =>
                        run("apr", async () => {
                          await api("/approvals/decide", {
                            method: "POST",
                            headers: { "X-Owner-Secret": ownerSecret },
                            body: JSON.stringify({ approval_id: a.id, approved: true }),
                          });
                        })
                      }
                    >
                      Approve
                    </button>
                    <button
                      className="danger"
                      onClick={() =>
                        run("apr", async () => {
                          await api("/approvals/decide", {
                            method: "POST",
                            headers: { "X-Owner-Secret": ownerSecret },
                            body: JSON.stringify({ approval_id: a.id, approved: false }),
                          });
                        })
                      }
                    >
                      Reject
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="panel span-6">
            <h2>Projects & previews</h2>
            <table>
              <thead>
                <tr>
                  <th>Project</th>
                  <th>Status</th>
                  <th>Preview</th>
                </tr>
              </thead>
              <tbody>
                {data.projects.map((p) => (
                  <tr key={String(p.id)}>
                    <td>{String(p.id)}</td>
                    <td>{String(p.status)}</td>
                    <td>
                      {p.preview_path ? (
                        <a href={`/api/proxy/preview/${p.id}`} target="_blank" rel="noreferrer">
                          Open
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="panel span-6">
            <h2>Invoices</h2>
            <table>
              <thead>
                <tr>
                  <th>Lifecycle</th>
                  <th>Amount</th>
                  <th>Provider</th>
                </tr>
              </thead>
              <tbody>
                {data.invoices.map((inv) => (
                  <tr key={String(inv.id)}>
                    <td>
                      {String(inv.lifecycle)}{" "}
                      {inv.simulated ? <span className="tag sim">SIMULATED</span> : null}
                    </td>
                    <td>{usd(Number(inv.amount_cents))}</td>
                    <td>{String(inv.provider)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="panel span-6">
            <h2>Payout configuration</h2>
            <p className="muted">
              Private owner screen. Agents cannot modify the beneficiary.
              If Kotak cannot link to US Stripe, use Payoneer/Wise or marketplace
              escrow — see docs/INDIA-PAYOUTS.md. UPI does not accept USD.
            </p>
            {data.payouts.configured ? (
              <div className="stack" style={{ margin: "0.75rem 0" }}>
                <div className="row"><span className="muted">Account</span><span>•••• {String(data.payouts.bank_account_last4)}</span></div>
                <div className="row"><span className="muted">Bank</span><span>{String(data.payouts.bank_name || "—")}</span></div>
                <div className="row"><span className="muted">UPI</span><span>{String(data.payouts.upi_id_masked || "—")}</span></div>
                <p className="muted">{String(data.payouts.note || "")}</p>
              </div>
            ) : (
              <p className="muted">Not configured.</p>
            )}
            <div className="form-grid">
              <label>
                Owner secret
                <input value={ownerSecret} onChange={(e) => setOwnerSecret(e.target.value)} type="password" autoComplete="off" />
              </label>
              <label>
                Session token
                <input value={payoutSession} onChange={(e) => setPayoutSession(e.target.value)} readOnly placeholder="Authenticate first" />
              </label>
              <label>
                Payout provider (payoneer / wise / stripe_india / kotak_swift / upi_inr)
                <input
                  value={payoutForm.provider || "payoneer"}
                  onChange={(e) => setPayoutForm({ ...payoutForm, provider: e.target.value })}
                />
              </label>
              <label>
                Account holder
                <input value={payoutForm.account_holder} onChange={(e) => setPayoutForm({ ...payoutForm, account_holder: e.target.value })} />
              </label>
              <label>
                Bank name
                <input value={payoutForm.bank_name} onChange={(e) => setPayoutForm({ ...payoutForm, bank_name: e.target.value })} />
              </label>
              <label>
                Account number
                <input value={payoutForm.bank_account_number} onChange={(e) => setPayoutForm({ ...payoutForm, bank_account_number: e.target.value })} autoComplete="off" />
              </label>
              <label>
                Routing / IFSC
                <input value={payoutForm.routing_or_ifsc} onChange={(e) => setPayoutForm({ ...payoutForm, routing_or_ifsc: e.target.value })} autoComplete="off" />
              </label>
              <label>
                UPI ID (INR domestic)
                <input value={payoutForm.upi_id} onChange={(e) => setPayoutForm({ ...payoutForm, upi_id: e.target.value })} autoComplete="off" />
              </label>
            </div>
            <div className="actions" style={{ marginTop: "0.75rem" }}>
              <button
                onClick={() =>
                  run("payout-auth", async () => {
                    const r = await api("/payouts/auth", {
                      method: "POST",
                      headers: { "X-Owner-Secret": ownerSecret },
                      body: JSON.stringify({ owner_secret: ownerSecret }),
                    });
                    setPayoutSession(r.session_token);
                  })
                }
              >
                Reauthenticate
              </button>
              <button
                className="primary"
                onClick={() =>
                  run("payout-save", async () => {
                    await api("/payouts", {
                      method: "POST",
                      headers: { "X-Payout-Session": payoutSession },
                      body: JSON.stringify({ ...payoutForm, session_token: payoutSession }),
                    });
                    setPayoutSession("");
                    setPayoutForm({ ...payoutForm, bank_account_number: "", routing_or_ifsc: "", upi_id: "" });
                  })
                }
              >
                Save payout (encrypted)
              </button>
            </div>
          </section>

          <section className="panel span-6">
            <h2>Integration status</h2>
            <div className="stack">
              {Object.entries(data.integrations).map(([k, v]) => (
                <div className="row" key={k}>
                  <span className="muted">{k}</span>
                  <span className="tag warn">
                    {v}
                  </span>
                </div>
              ))}
            </div>
            <p className="muted" style={{ marginTop: "0.75rem" }}>
              {data.targets.label}. Aspirational rate ${data.targets.aspirational_rate_usd_per_hour}/h ·
              first milestone ${data.targets.cash_milestone_usd.toLocaleString()}.
            </p>
          </section>
        </div>
      )}
    </main>
  );
}

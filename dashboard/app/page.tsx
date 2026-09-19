"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

type Product = {
  id: string;
  slug: string;
  title: string;
  niche: string;
  description: string;
  sell_price_cents: number;
  status: string;
  source_market: string;
};

type Commerce = {
  ceo: string;
  title: string;
  company: string;
  targets: {
    daily_sales_usd: number;
    monthly_sales_usd: number;
    billion_vision_usd: number;
    label: string;
  };
  settled_sales_usd: number;
  month_settled_sales_usd: number;
  catalog: { total: number; published: number; draft: number; compliance_hold: number };
  blocker: string;
  published_products: Product[];
  products: Product[];
  payment_note: string;
};

function money(centsOrUsd: number, alreadyUsd = true) {
  const n = alreadyUsd ? centsOrUsd : centsOrUsd / 100;
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
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

export default function StorefrontPage() {
  const [data, setData] = useState<Commerce | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [booting, setBooting] = useState(false);

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
    const t = setInterval(refresh, 15000);
    return () => clearInterval(t);
  }, [refresh]);

  async function bootCompany() {
    setBooting(true);
    try {
      const secret = sessionStorage.getItem("gq_owner_secret") || prompt("Owner secret (FIRM_OWNER_SECRET)") || "";
      if (secret) sessionStorage.setItem("gq_owner_secret", secret);
      await api("/commerce/run", {
        method: "POST",
        headers: { "X-Owner-Secret": secret },
        body: JSON.stringify({ publish: true, outreach: true }),
      });
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBooting(false);
    }
  }

  const products = data?.published_products?.length
    ? data.published_products
    : (data?.products || []).filter((p) => p.status !== "compliance_hold");

  return (
    <div className="gq">
      <header className="gq-nav">
        <Link href="/" className="gq-logo">
          Grey Quantum
        </Link>
        <nav>
          <Link href="/#catalog">Catalog</Link>
          <Link href="/ceo">CEO Command</Link>
          <Link href="/ops">Firm Ops</Link>
        </nav>
      </header>

      <section className="gq-hero">
        <div className="gq-hero-plane" aria-hidden="true" />
        <div className="gq-hero-copy">
          <p className="gq-brand">Grey Quantum</p>
          <h1>Trade the loop. Ship the world.</h1>
          <p className="gq-lede">
            Anestasis Grey runs a real buy→ship→sell storefront for India and global buyers —
            margin-checked products, drafted ads, unpaid reservations until payment rails confirm cash.
          </p>
          <div className="gq-cta">
            <a className="gq-btn primary" href="#catalog">
              Browse products
            </a>
            <button className="gq-btn" type="button" onClick={bootCompany} disabled={booting}>
              {booting ? "Running teams…" : "CEO: run daily cycle"}
            </button>
          </div>
        </div>
      </section>

      {error && <p className="gq-error">{error}</p>}

      <section className="gq-strip" aria-label="Targets">
        <div>
          <span className="label">Daily target</span>
          <strong>{money(data?.targets.daily_sales_usd ?? 10000)}</strong>
        </div>
        <div>
          <span className="label">Monthly target</span>
          <strong>{money(data?.targets.monthly_sales_usd ?? 500000)}</strong>
        </div>
        <div>
          <span className="label">Settled sales</span>
          <strong>{money(data?.settled_sales_usd ?? 0)}</strong>
        </div>
        <div>
          <span className="label">Published SKUs</span>
          <strong>{data?.catalog.published ?? 0}</strong>
        </div>
      </section>
      <p className="gq-note">{data?.targets.label || "Targets only — not guarantees."}</p>

      <section id="catalog" className="gq-catalog">
        <h2>Catalog</h2>
        <p className="gq-section-lede">
          One job: sell margin-positive products with honest sourcing notes. Compliance-held items stay off sale.
        </p>
        {!products.length && (
          <p className="gq-empty">
            No products yet. Use <strong>CEO: run daily cycle</strong> (owner secret) to scan loops and publish pages.
          </p>
        )}
        <ul className="gq-list">
          {products.map((p, i) => (
            <li key={p.id} style={{ animationDelay: `${80 + i * 60}ms` }}>
              <Link href={`/product/${p.slug}`}>
                <span className="niche">{p.niche.replace(/_/g, " ")}</span>
                <span className="title">{p.title}</span>
                <span className="price">{money(p.sell_price_cents / 100)}</span>
                <span className="status">{p.status}</span>
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <section className="gq-model">
        <h2>Revenue model</h2>
        <p>
          Source wholesale or public B2B listings → verify landed cost → publish store page → draft Meta / YouTube /
          Instagram ads → collect payment via Stripe or Razorpay → fulfill and ship. Settled revenue only after
          provider confirmation. Indian Kotak / UPI payout details are added when you are ready.
        </p>
        <p className="gq-note">{data?.blocker}</p>
      </section>

      <footer className="gq-foot">
        <span>CEO {data?.ceo || "Anestasis Grey"} · Quantum Brain command</span>
        <span>India + world shipping</span>
      </footer>
    </div>
  );
}

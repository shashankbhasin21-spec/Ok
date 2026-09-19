"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

type Product = {
  id: string;
  slug: string;
  title: string;
  niche: string;
  description: string;
  sell_price_cents: number;
  source_price_cents: number;
  shipping_estimate_cents: number;
  margin_cents: number;
  status: string;
  evidence_note: string;
  source_market: string;
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

export default function ProductPage() {
  const params = useParams();
  const slug = String(params.slug || "");
  const [product, setProduct] = useState<Product | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    customer_email: "",
    customer_name: "",
    ship_country: "IN",
  });

  useEffect(() => {
    if (!slug) return;
    api(`/commerce/products/${slug}`)
      .then(setProduct)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [slug]);

  async function checkout(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setOk(null);
    setError(null);
    try {
      const res = await api("/commerce/checkout", {
        method: "POST",
        body: JSON.stringify({ product_slug: slug, ...form }),
      });
      setOk(
        `Order ${res.order.id} reserved (${res.order.status}). ${res.next}`
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!product && !error) {
    return (
      <div className="gq gq-pad">
        <p className="gq-note">Loading…</p>
      </div>
    );
  }

  return (
    <div className="gq gq-pad">
      <header className="gq-nav">
        <Link href="/" className="gq-logo">
          Grey Quantum
        </Link>
        <nav>
          <Link href="/#catalog">Catalog</Link>
          <Link href="/ceo">CEO</Link>
        </nav>
      </header>

      {error && <p className="gq-error">{error}</p>}
      {ok && <p className="gq-ok">{ok}</p>}

      {product && (
        <article className="gq-product">
          <p className="niche">{product.niche.replace(/_/g, " ")} · {product.source_market}</p>
          <h1>{product.title}</h1>
          <p className="price">${(product.sell_price_cents / 100).toFixed(2)} USD</p>
          <p className="desc">{product.description}</p>
          <p className="gq-note">Sourcing: {product.evidence_note}</p>
          <p className="gq-note">
            Hypothesis margin after shipping/fees: ${(product.margin_cents / 100).toFixed(2)} — not
            profit until paid and fulfilled.
          </p>

          {product.status === "published" ? (
            <form className="gq-form" onSubmit={checkout}>
              <label>
                Email
                <input
                  required
                  type="email"
                  value={form.customer_email}
                  onChange={(e) => setForm({ ...form, customer_email: e.target.value })}
                />
              </label>
              <label>
                Name
                <input
                  type="text"
                  value={form.customer_name}
                  onChange={(e) => setForm({ ...form, customer_name: e.target.value })}
                />
              </label>
              <label>
                Ship to
                <select
                  value={form.ship_country}
                  onChange={(e) => setForm({ ...form, ship_country: e.target.value })}
                >
                  <option value="IN">India</option>
                  <option value="US">United States</option>
                  <option value="EU">Europe</option>
                  <option value="GLOBAL">Other</option>
                </select>
              </label>
              <button className="gq-btn primary" type="submit" disabled={busy}>
                {busy ? "Reserving…" : "Reserve order (pay next)"}
              </button>
              <p className="gq-note">
                Creates an unpaid order only. Cash counts after Stripe/Razorpay confirmation.
              </p>
            </form>
          ) : (
            <p className="gq-hold">Not for sale yet — status: {product.status}</p>
          )}
        </article>
      )}
    </div>
  );
}

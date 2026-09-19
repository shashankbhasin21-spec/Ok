"""Generate standalone product HTML pages for the public store."""

from __future__ import annotations

import html
from pathlib import Path

from . import CEO_NAME, COMPANY_NAME


def render_product_page(product: dict) -> str:
    title = html.escape(product["title"])
    desc = html.escape(product["description"])
    niche = html.escape(product["niche"].replace("_", " "))
    price = product["sell_price_cents"] / 100
    margin = product["margin_cents"] / 100
    evidence = html.escape(product.get("evidence_note") or "")
    status = html.escape(product["status"])
    slug = html.escape(product["slug"])

    buy_block = (
        f'<form class="buy" method="post" action="/api/proxy/commerce/orders">'
        f'<input type="hidden" name="product_slug" value="{slug}"/>'
        f'<label>Email<input name="customer_email" type="email" required/></label>'
        f'<label>Name<input name="customer_name" type="text"/></label>'
        f'<label>Ship to'
        f'<select name="ship_country"><option value="IN">India</option>'
        f'<option value="US">United States</option>'
        f'<option value="EU">Europe</option>'
        f'<option value="GLOBAL">Other</option></select></label>'
        f'<button type="submit">Reserve order (pay next)</button>'
        f'<p class="fine">Creates an unpaid order. Money moves only after a real payment provider confirms.</p>'
        f"</form>"
        if status == "published"
        else f'<p class="hold">Status: <strong>{status}</strong> — not for sale until published and payment rails are live.</p>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{title} — {COMPANY_NAME}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Syne:wght@600;700;800&display=swap" rel="stylesheet"/>
  <style>
    :root {{
      --bg: #0c0f12; --ink: #e8e2d6; --muted: #9a9286; --copper: #c4783a;
      --line: rgba(232,226,214,0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; min-height: 100vh; color: var(--ink);
      font-family: Manrope, sans-serif;
      background:
        radial-gradient(ellipse 80% 50% at 70% 0%, rgba(196,120,58,0.18), transparent 55%),
        radial-gradient(ellipse 60% 40% at 10% 80%, rgba(80,110,140,0.15), transparent 50%),
        linear-gradient(165deg, #0c0f12 0%, #14181e 45%, #0a0c0f 100%);
    }}
    .wrap {{ max-width: 880px; margin: 0 auto; padding: 2rem 1.25rem 4rem; }}
    .brand {{ font-family: Syne, sans-serif; font-weight: 800; letter-spacing: -0.03em; font-size: 1.1rem; color: var(--copper); text-decoration: none; }}
    h1 {{ font-family: Syne, sans-serif; font-size: clamp(2rem, 5vw, 3rem); line-height: 1.05; margin: 1.2rem 0 0.6rem; }}
    .meta {{ color: var(--muted); font-size: 0.9rem; }}
    .price {{ font-family: Syne, sans-serif; font-size: 2rem; color: var(--copper); margin: 1rem 0; }}
    .desc {{ line-height: 1.55; max-width: 58ch; }}
    .buy, .hold {{ margin-top: 2rem; padding-top: 1.5rem; border-top: 1px solid var(--line); }}
    label {{ display: flex; flex-direction: column; gap: 0.3rem; margin-bottom: 0.75rem; font-size: 0.85rem; color: var(--muted); }}
    input, select, button {{ font: inherit; padding: 0.65rem 0.75rem; border-radius: 4px; border: 1px solid var(--line); background: rgba(255,255,255,0.04); color: var(--ink); }}
    button {{ background: var(--copper); border-color: transparent; color: #120e0a; font-weight: 700; cursor: pointer; width: 100%; margin-top: 0.5rem; }}
    .fine, .evidence {{ color: var(--muted); font-size: 0.8rem; margin-top: 0.75rem; }}
    .ceo {{ margin-top: 3rem; color: var(--muted); font-size: 0.85rem; }}
  </style>
</head>
<body>
  <div class="wrap">
    <a class="brand" href="/">{COMPANY_NAME}</a>
    <p class="meta">{niche} · {status}</p>
    <h1>{title}</h1>
    <div class="price">${price:.2f} USD</div>
    <p class="desc">{desc}</p>
    {buy_block}
    <p class="evidence">Sourcing note: {evidence}</p>
    <p class="evidence">Illustrative unit margin after shipping/fees hypothesis: ${margin:.2f} (not profit until paid + fulfilled).</p>
    <p class="ceo">Operated by {CEO_NAME}, {COMPANY_NAME}.</p>
  </div>
</body>
</html>
"""


def write_product_page(workdir: Path, product: dict) -> Path:
    pages = workdir / "product_pages"
    pages.mkdir(parents=True, exist_ok=True)
    path = pages / f"{product['slug']}.html"
    path.write_text(render_product_page(product), encoding="utf-8")
    return path

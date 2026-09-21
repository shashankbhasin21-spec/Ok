import { el, emptyState } from "../ui.js";

export default async function renderAccount(root, ctx) {
  const { api, store, navigate } = ctx;
  const key = api.getApiKey();
  const h = store.health;

  root.append(el("div", { className: "ls-page", style: "max-width:560px" }, [
    el("div", { className: "ls-panel", style: "padding:1.25rem;display:flex;flex-direction:column;gap:0.85rem" }, [
      el("h2", { text: "Account" }),
      el("p", { className: "ls-field__hint", text: "This studio authenticates with a gateway API key. There is no separate user profile fabricator." }),
      el("div", { className: "ls-diag" }, [
        el("div", { className: "ls-diag__item" }, [
          el("div", { className: "ls-diag__label", text: "API key" }),
          el("div", { className: "ls-diag__value", text: key ? "SET" : "MISSING" }),
        ]),
        el("div", { className: "ls-diag__item" }, [
          el("div", { className: "ls-diag__label", text: "Gateway" }),
          el("div", { className: "ls-diag__value", text: h?.gateway === "ok" ? "ONLINE" : "UNKNOWN" }),
          el("div", { className: "ls-diag__detail", text: h?.version ? `v${h.version}` : "" }),
        ]),
        el("div", { className: "ls-diag__item" }, [
          el("div", { className: "ls-diag__label", text: "Auth required" }),
          el("div", { className: "ls-diag__value", text: h?.details?.auth_required ? "YES" : h ? "NO (dev)" : "—" }),
        ]),
      ]),
      el("button", { type: "button", className: "ls-btn ls-btn--primary", text: "Manage API key", onClick: () => navigate("settings") }),
    ]),
    !key ? emptyState({
      title: "Not authenticated",
      body: "Add an API key to load jobs, providers, metrics, and assets.",
    }) : null,
  ]));

  return () => {};
}

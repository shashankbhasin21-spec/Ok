/**
 * Account / session — API key status + gateway version. No fake profiles.
 */

import { el, clear, emptyState, skeleton, statusBadge, abridgeId } from "../ui.js";

export async function renderAccount(root, ctx) {
  const { api, store, navigate } = ctx;
  clear(root);

  const page = el("div", { className: "ls-page" });
  page.append(el("header", { className: "ls-hero ls-hero--compact" }, [
    el("div", { className: "ls-hero__brand", text: "Lumen" }),
    el("h2", { className: "ls-hero__title", text: "Account" }),
    el("p", { className: "ls-hero__lede", text: "Session is an API key — there is no separate user profile." }),
  ]));

  const body = el("div", { className: "ls-panel" });
  body.append(skeleton());
  page.append(body);
  root.append(page);

  const key = api.getApiKey() || "";
  const hasKey = !!String(key).trim();

  let health = store.health;
  let healthError = null;
  try {
    health = await api.health();
    store.health = health;
  } catch (err) {
    healthError = err.message || "Health check failed";
  }

  body.replaceChildren(
    el("h3", { text: "Session" }),
    el("div", { className: "ls-stat-row" }, [
      el("div", { className: "ls-stat" }, [
        statusBadge(hasKey ? "SET" : "MISSING"),
        el("div", { className: "ls-stat__label", text: "API key" }),
      ]),
      el("div", { className: "ls-stat" }, [
        statusBadge(health && !healthError ? "ONLINE" : "OFFLINE"),
        el("div", { className: "ls-stat__label", text: "Gateway" }),
      ]),
    ]),
    hasKey
      ? el("p", {
          className: "ls-field__hint",
          text: `Key present (length ${String(key).length}; preview ${abridgeId(key)}). Value is never displayed in full.`,
        })
      : el("p", {
          className: "ls-composer__gate",
          text: "No API key in this browser. Authenticated /v1 routes will fail until you add one in Settings.",
        }),
    el("h3", { text: "Gateway" }),
    health
      ? el("div", {}, [
          el("p", { text: `Version: ${health.version || "—"}` }),
          el("p", { text: `Status: ${health.status || health.gateway || "—"}` }),
          el("p", { text: `generation_available: ${health.generation_available === true ? "true" : "false"}` }),
          el("p", { text: `Ready engines: ${(health.ready_engines || []).join(", ") || "—"}` }),
        ])
      : emptyState({ title: "Gateway unreachable", body: healthError || "No health payload" }),
    el("div", { className: "ls-toolbar" }, [
      el("button", {
        type: "button",
        className: "ls-btn ls-btn--primary",
        text: "Open settings",
        onClick: () => navigate("settings"),
      }),
      el("button", {
        type: "button",
        className: "ls-btn",
        text: "System diagnostics",
        onClick: () => navigate("system"),
      }),
    ])
  );

  return () => {};
}

export default renderAccount;

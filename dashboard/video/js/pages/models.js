/**
 * Models / engine browser — readiness from provider.ready only.
 */

import {
  el,
  clear,
  statusBadge,
  engineCapabilities,
  emptyState,
  skeleton,
} from "../ui.js";

export async function renderModels(root, ctx) {
  const { api, store, navigate } = ctx;
  clear(root);

  const page = el("div", { className: "ls-page" });
  page.append(el("header", { className: "ls-hero ls-hero--compact" }, [
    el("div", { className: "ls-hero__brand", text: "Lumen" }),
    el("h2", { className: "ls-hero__title", text: "Models" }),
    el("p", { className: "ls-hero__lede", text: "Engine catalog from providers. READY means provider.ready — not gateway HTTP alone." }),
  ]));

  const healthStrip = el("div", { className: "ls-stat-row", "aria-live": "polite" });
  const grid = el("div", { className: "ls-grid ls-grid--engines" });
  grid.append(skeleton());
  page.append(healthStrip, grid);
  root.append(page);

  function paintHealth(health) {
    const loading = health?.model_loading;
    let loadingLabel = "—";
    if (loading && typeof loading === "object") {
      const keys = Object.keys(loading).filter((k) => loading[k]);
      loadingLabel = keys.length ? keys.join(", ") : "none";
    }
    healthStrip.replaceChildren(
      el("div", { className: "ls-stat" }, [
        el("div", { className: "ls-stat__value", text: String((health?.ready_engines || []).length) }),
        el("div", { className: "ls-stat__label", text: "Ready engines (health)" }),
      ]),
      el("div", { className: "ls-stat" }, [
        el("div", { className: "ls-stat__value", text: String((health?.installed_engines || []).length) }),
        el("div", { className: "ls-stat__label", text: "Installed" }),
      ]),
      el("div", { className: "ls-stat" }, [
        el("div", { className: "ls-stat__value", text: health?.generation_available === true ? "yes" : "no" }),
        el("div", { className: "ls-stat__label", text: "generation_available" }),
      ]),
      el("div", { className: "ls-stat" }, [
        el("div", { className: "ls-stat__value", text: loadingLabel }),
        el("div", { className: "ls-stat__label", text: "Model loading" }),
      ])
    );
  }

  function card(provider, health) {
    const ready = provider.ready === true;
    const details = provider.details || {};
    const models = details.models || [];
    const tasks = provider.tasks || [];
    return el("article", { className: `ls-panel ls-engine${ready ? " is-ready" : ""}` }, [
      el("div", { className: "ls-engine__top" }, [
        el("div", { className: "ls-engine__name", text: provider.name }),
        statusBadge(ready ? "AVAILABLE" : provider.status || "UNAVAILABLE"),
      ]),
      el("p", { text: `${ready ? "Ready" : "Not ready"} · installed: ${provider.installed ? "yes" : "no"}` }),
      el("p", { text: `Min VRAM: ${provider.min_vram_gb != null ? `${provider.min_vram_gb} GB` : "—"}` }),
      el("p", { text: `License: ${provider.license || "—"}` }),
      el("div", { className: "ls-engine__tasks" }, [
        el("span", { className: `ls-badge${tasks.includes("text-to-video") ? " ls-badge--ok" : ""}`, text: "T2V" }),
        el("span", { className: `ls-badge${tasks.includes("image-to-video") ? " ls-badge--ok" : ""}`, text: "I2V" }),
        tasks.includes("video-extend") ? el("span", { className: "ls-badge ls-badge--ok", text: "Extend" }) : null,
      ]),
      el("p", { className: "ls-field__hint", text: `Tasks: ${tasks.join(", ") || "—"}` }),
      models.length
        ? el("ul", { className: "ls-list" }, models.map((m) => el("li", { text: m })))
        : el("p", { className: "ls-field__hint", text: "No model list in catalog details." }),
      details.preferred_quality
        ? el("p", { className: "ls-field__hint", text: `Preferred quality: ${(details.preferred_quality || []).join(", ")}` })
        : null,
      el("p", { className: "ls-field__hint", text: `In health.ready_engines: ${(health?.ready_engines || []).includes(provider.name) ? "yes" : "no"}` }),
      !ready
        ? el("p", { className: "ls-composer__gate", text: "This engine will not be offered as READY for generation." })
        : null,
    ]);
  }

  async function load() {
    try {
      const [health, providers] = await Promise.all([
        api.health().catch(() => store.health),
        api.getApiKey() ? api.providers() : Promise.resolve(store.providers || []),
      ]);
      if (health) store.health = health;
      const list = Array.isArray(providers) ? providers : [];
      if (list.length) store.providers = list;
      const caps = engineCapabilities(store.providers);
      paintHealth(store.health);
      grid.replaceChildren();
      if (!store.providers.length) {
        grid.append(emptyState({
          title: "No providers",
          body: api.getApiKey()
            ? "Authenticate and ensure the gateway responds."
            : "Set an API key in Settings to load engines.",
          action: el("button", {
            type: "button",
            className: "ls-btn",
            text: "Settings",
            onClick: () => navigate("settings"),
          }),
        }));
      } else {
        store.providers.forEach((p) => grid.append(card(p, store.health)));
      }
      if (store.health?.ready_engines?.length && !caps.anyReady) {
        page.append(el("p", {
          className: "ls-composer__gate",
          text: "Health lists ready_engines but no provider.ready=true — UI trusts provider.ready for CTA gating.",
        }));
      }
    } catch (err) {
      grid.replaceChildren(emptyState({ title: "Could not load models", body: err.message || "Error" }));
    }
  }

  await load();
  const unsub = store.subscribe((type) => {
    if (type === "health" || type === "data") load();
  });

  return () => unsub();
}

export default renderModels;

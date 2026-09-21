import { el, emptyState, statusBadge } from "../ui.js";
import { stagger } from "../motion.js";

export default async function renderModels(root, ctx) {
  const { api, store, navigate } = ctx;
  const host = el("div", { className: "ls-grid ls-grid--engines" });
  const loading = store.health?.model_loading || {};

  root.append(el("div", { className: "ls-page" }, [
    el("header", { className: "ls-hero" }, [
      el("h2", { className: "ls-hero__title", text: "Engine center" }),
      el("p", { className: "ls-hero__lede", text: "Availability and readiness come from provider capability state — never from worker HTTP health alone." }),
    ]),
    host,
  ]));

  function render() {
    host.replaceChildren();
    if (!api.getApiKey()) {
      host.append(emptyState({
        title: "API key required",
        body: "Providers are authenticated.",
        action: el("button", { type: "button", className: "ls-btn ls-btn--primary", text: "Settings", onClick: () => navigate("settings") }),
      }));
      return;
    }
    const providers = store.providers || [];
    if (!providers.length) {
      host.append(emptyState({ title: "No engines configured", body: "The gateway returned an empty provider list." }));
      return;
    }
    providers.forEach((p) => {
      const tasks = p.tasks || [];
      const loadState = loading[p.name] || loading[p.name?.toLowerCase?.()] || null;
      host.append(el("article", { className: "ls-panel ls-engine" }, [
        el("div", { className: "ls-engine__top" }, [
          el("div", { className: "ls-engine__name", text: p.name }),
          statusBadge(p.ready ? "AVAILABLE" : p.status),
        ]),
        el("div", { className: "ls-engine__tasks" }, [
          el("span", { className: `ls-badge ${tasks.includes("text-to-video") ? "ls-badge--ok" : ""}`, text: `T2V ${tasks.includes("text-to-video") ? "yes" : "no"}` }),
          el("span", { className: `ls-badge ${tasks.includes("image-to-video") ? "ls-badge--ok" : ""}`, text: `I2V ${tasks.includes("image-to-video") ? "yes" : "no"}` }),
          ...tasks.filter((t) => !["text-to-video", "image-to-video"].includes(t)).map((t) => el("span", { className: "ls-badge", text: t })),
        ]),
        el("div", { className: "ls-engine__meta" }, [
          el("span", { text: `Installed: ${p.installed ? "yes" : "no"}` }),
          el("span", { text: `Ready: ${p.ready ? "yes" : "no"}` }),
          el("span", { text: p.min_vram_gb != null ? `GPU / VRAM req ~${p.min_vram_gb} GB` : "GPU requirement unknown" }),
          el("span", { text: loadState ? `Model loading: ${typeof loadState === "string" ? loadState : "in progress"}` : "Model loading: idle" }),
          el("span", { text: "Supported duration: engine-dependent (gateway accepts 1–600s; social default ≥30s)" }),
          el("span", { text: "Supported resolution: derived from aspect unless overridden" }),
          p.license ? el("span", { text: `License: ${p.license}` }) : null,
          p.upstream ? el("a", { href: p.upstream, target: "_blank", rel: "noopener noreferrer", text: "Upstream" }) : null,
        ]),
      ]));
    });
    stagger(host);
  }

  render();
  return store.subscribe(() => render());
}

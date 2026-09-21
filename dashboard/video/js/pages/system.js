import { el, emptyState, statusBadge } from "../ui.js";

function diag(label, value, detail = "") {
  return el("div", { className: "ls-diag__item" }, [
    el("div", { className: "ls-diag__label", text: label }),
    el("div", { className: "ls-diag__value", text: value }),
    detail ? el("div", { className: "ls-diag__detail", text: detail }) : null,
  ]);
}

export default async function renderSystem(root, ctx) {
  const { api, store, navigate } = ctx;
  const host = el("div", { className: "ls-page" });
  root.append(host);

  function render() {
    host.replaceChildren();
    const h = store.health;
    if (!h) {
      host.append(emptyState({
        title: "Gateway unreachable",
        body: "Could not load /health. The studio shell is offline relative to the API.",
      }));
      return;
    }

    const details = h.details || {};
    const worker = h.worker || {};
    const cuda = details.cuda_available;
    const gpuName = worker.gpu_name || worker.device_name || worker.gpu || null;
    // Sanitize: never show paths, tokens, URLs with secrets
    const gpuInfo = gpuName ? String(gpuName).slice(0, 80) : (cuda === true ? "CUDA device present" : "n/a");
    const vramUsed = details.vram_total_mb != null && details.vram_free_mb != null
      ? `${Math.max(0, Number(details.vram_total_mb) - Number(details.vram_free_mb))} / ${details.vram_total_mb} MB`
      : null;
    const metrics = store.metrics?.totals || {};
    const loading = h.model_loading && Object.keys(h.model_loading).length
      ? JSON.stringify(h.model_loading)
      : "idle";

    host.append(
      el("header", { className: "ls-hero" }, [
        el("h2", { className: "ls-hero__title", text: "System status" }),
        el("p", { className: "ls-hero__lede", text: "Sanitized diagnostics. Secrets, tokens, filesystem paths, and signed URLs are never shown." }),
      ]),
      el("div", { className: "ls-diag" }, [
        diag("Gateway", h.gateway === "ok" ? "ONLINE" : "OFFLINE", `version ${h.version || "—"}`),
        diag("Worker", h.gpu_worker_available ? "CONNECTED" : "DISCONNECTED", worker.worker_id ? `id ${String(worker.worker_id).slice(0, 12)}…` : ""),
        diag("CUDA", cuda === true ? "AVAILABLE" : cuda === false ? "UNAVAILABLE" : "UNKNOWN"),
        diag("GPU", gpuInfo),
        diag("VRAM", vramUsed || "n/a", details.vram_free_mb != null ? `free ${details.vram_free_mb} MB` : ""),
        diag("Generation", h.generation_available ? "AVAILABLE" : "BLOCKED"),
        diag("Ready engines", (h.ready_engines || []).join(", ") || "none"),
        diag("Installed engines", (h.installed_engines || []).join(", ") || "none"),
        diag("Queue depth", String(h.queue_depth ?? "—")),
        diag("Model loading", loading.slice(0, 120)),
        diag("Successes", String(metrics.completed ?? "—"), "from stored metrics"),
        diag("Failures", String(metrics.failed ?? "—"), "from stored metrics"),
      ]),
      el("section", { className: "ls-panel", style: "padding:1.15rem;margin-top:1rem" }, [
        el("div", { className: "ls-section-hd" }, [
          el("div", {}, [el("h3", { text: "Provider readiness" }), el("p", { text: "Ready requires installed models + CUDA capability — not mere HTTP ping" })]),
          el("button", { type: "button", className: "ls-btn ls-btn--ghost ls-btn--sm", text: "Models", onClick: () => navigate("models") }),
        ]),
        el("div", { className: "ls-grid ls-grid--engines" }, (store.providers || []).map((p) =>
          el("div", { className: "ls-engine ls-panel", style: "padding:1rem" }, [
            el("div", { className: "ls-engine__top" }, [
              el("strong", { text: p.name }),
              statusBadge(p.status),
            ]),
            el("div", { className: "ls-field__hint", text: `ready=${p.ready} · installed=${p.installed}` }),
          ])
        )),
        !(store.providers || []).length ? el("p", { className: "ls-field__hint", text: api.getApiKey() ? "No providers loaded." : "Set API key to load providers." }) : null,
      ]),
    );
  }

  render();
  return store.subscribe(() => render());
}

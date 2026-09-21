/**
 * System diagnostics — sanitized health + metrics, no secrets/paths.
 */

import { el, clear, emptyState, skeleton, statusBadge } from "../ui.js";

function formatMetric(n, { digits = 1, asPercent = false } = {}) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  let v = Number(n);
  if (asPercent) v *= 100;
  return `${Number.isInteger(v) ? v : v.toFixed(digits)}${asPercent ? "%" : ""}`;
}

function sanitizeGpuLabel(worker, details) {
  const name =
    details?.gpu_name ||
    worker?.gpu_name ||
    worker?.device_name ||
    (details?.cuda_available === true ? "CUDA device" : null);
  if (!name) return "—";
  return String(name).replace(/\/[^\s]+/g, "").slice(0, 80) || "CUDA device";
}

export async function renderSystem(root, ctx) {
  const { api, store } = ctx;
  clear(root);

  const page = el("div", { className: "ls-page" });
  page.append(el("header", { className: "ls-hero ls-hero--compact" }, [
    el("div", { className: "ls-hero__brand", text: "Lumen" }),
    el("h2", { className: "ls-hero__title", text: "System" }),
    el("p", { className: "ls-hero__lede", text: "Gateway and worker diagnostics — sanitized, evidence-based." }),
  ]));

  const body = el("div", { className: "ls-grid" });
  body.append(skeleton());
  page.append(body);
  root.append(page);

  try {
    const health = await api.health();
    store.health = health;
    const metrics = api.getApiKey()
      ? await api.metrics().catch(() => store.metrics)
      : store.metrics;
    if (metrics) store.metrics = metrics;

    const gatewayOnline = !!health && (health.status === "ok" || health.gateway === "ok" || !!health.version);
    const worker = health?.worker;
    const workerConnected =
      health?.gpu_worker_available === true ||
      (worker && (worker.ok === true || worker.connected === true || worker.status === "ok"));
    const details = health?.details || {};
    const cuda = details.cuda_available;

    body.replaceChildren(
      el("section", { className: "ls-panel" }, [
        el("h3", { text: "Connectivity" }),
        el("div", { className: "ls-stat-row" }, [
          el("div", { className: "ls-stat" }, [
            statusBadge(gatewayOnline ? "ONLINE" : "OFFLINE"),
            el("div", { className: "ls-stat__label", text: "Gateway" }),
          ]),
          el("div", { className: "ls-stat" }, [
            statusBadge(workerConnected ? "CONNECTED" : "DISCONNECTED"),
            el("div", { className: "ls-stat__label", text: "Worker" }),
          ]),
          el("div", { className: "ls-stat" }, [
            statusBadge(cuda === true ? "AVAILABLE" : cuda === false ? "UNAVAILABLE" : "UNKNOWN"),
            el("div", { className: "ls-stat__label", text: "CUDA" }),
          ]),
        ]),
        el("p", { text: `Version: ${health?.version || "—"}` }),
        el("p", { text: `generation_available: ${health?.generation_available === true ? "true" : "false"}` }),
      ]),
      el("section", { className: "ls-panel" }, [
        el("h3", { text: "GPU" }),
        el("p", { text: `Device: ${sanitizeGpuLabel(worker, details)}` }),
        el("p", {
          text: `VRAM: ${details.vram_free_mb != null ? `${details.vram_free_mb} MB free` : "—"} / ${details.vram_total_mb != null ? `${details.vram_total_mb} MB total` : "—"}`,
        }),
        el("p", { className: "ls-field__hint", text: "Device identifiers and filesystem paths are omitted." }),
      ]),
      el("section", { className: "ls-panel" }, [
        el("h3", { text: "Engines & queue" }),
        el("p", { text: `Ready: ${(health?.ready_engines || []).join(", ") || "—"}` }),
        el("p", { text: `Installed: ${(health?.installed_engines || []).join(", ") || "—"}` }),
        el("p", { text: `Queue depth: ${health?.queue_depth != null ? health.queue_depth : "—"}` }),
        (() => {
          const ml = health?.model_loading;
          if (!ml || typeof ml !== "object") return el("p", { text: "Model loading: —" });
          const active = Object.entries(ml).filter(([, v]) => v);
          return el("p", { text: `Model loading: ${active.length ? active.map(([k]) => k).join(", ") : "none"}` });
        })(),
      ]),
      el("section", { className: "ls-panel" }, [
        el("h3", { text: "Outcomes (metrics)" }),
        metrics
          ? el("div", { className: "ls-stat-row" }, [
              el("div", { className: "ls-stat" }, [
                el("div", { className: "ls-stat__value", text: formatMetric(metrics.render_success_rate, { asPercent: true }) }),
                el("div", { className: "ls-stat__label", text: "Success rate" }),
              ]),
              el("div", { className: "ls-stat" }, [
                el("div", { className: "ls-stat__value", text: formatMetric(metrics.provider_failure_rate, { asPercent: true }) }),
                el("div", { className: "ls-stat__label", text: "Failure rate" }),
              ]),
              el("div", { className: "ls-stat" }, [
                el("div", {
                  className: "ls-stat__value",
                  text: metrics.totals?.completed != null
                    ? String(metrics.totals.completed)
                    : metrics.totals?.success != null
                      ? String(metrics.totals.success)
                      : "—",
                }),
                el("div", { className: "ls-stat__label", text: "Completed total" }),
              ]),
              el("div", { className: "ls-stat" }, [
                el("div", {
                  className: "ls-stat__value",
                  text: metrics.totals?.failed != null ? String(metrics.totals.failed) : "—",
                }),
                el("div", { className: "ls-stat__label", text: "Failed total" }),
              ]),
            ])
          : emptyState({ title: "No metrics", body: "Metrics endpoint returned nothing yet." }),
      ])
    );
  } catch (err) {
    body.replaceChildren(emptyState({
      title: "System unreachable",
      body: err.message || "Health check failed — gateway OFFLINE.",
    }));
  }

  return () => {};
}

export default renderSystem;

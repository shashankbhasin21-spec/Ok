import { el, emptyState } from "../ui.js";

function fmtRate(v) {
  if (v == null || Number.isNaN(Number(v))) return null;
  return `${(Number(v) * 100).toFixed(1)}%`;
}

function fmtSec(v) {
  if (v == null || Number.isNaN(Number(v))) return null;
  return `${Number(v).toFixed(1)}s`;
}

function metricCard(label, value) {
  const empty = value == null || value === "";
  return el("div", { className: "ls-metric" }, [
    el("div", { className: "ls-metric__label", text: label }),
    el("div", { className: `ls-metric__value${empty ? " is-empty" : ""}`, text: empty ? "No data yet" : String(value) }),
  ]);
}

export default async function renderAnalytics(root, ctx) {
  const { api, store, navigate } = ctx;
  const host = el("div", { className: "ls-page" });
  root.append(host);

  async function render() {
    host.replaceChildren();
    if (!api.getApiKey()) {
      host.append(emptyState({
        title: "API key required",
        body: "Analytics are computed from stored jobs only.",
        action: el("button", { type: "button", className: "ls-btn ls-btn--primary", text: "Settings", onClick: () => navigate("settings") }),
      }));
      return;
    }

    let m = store.metrics;
    try {
      m = await api.metrics();
      store.metrics = m;
    } catch (err) {
      host.append(emptyState({ title: "Metrics unavailable", body: err.message }));
      return;
    }

    const totals = m.totals || {};
    const hasJobs = (totals.jobs || 0) > 0;

    if (!hasJobs) {
      host.append(emptyState({
        title: "No analytics yet",
        body: "Charts and rates appear after real generations are stored. Nothing is fabricated.",
        action: el("button", { type: "button", className: "ls-btn ls-btn--primary", text: "Create", onClick: () => navigate("create") }),
      }));
      return;
    }

    const usage = m.engine_usage || {};
    const maxUsage = Math.max(1, ...Object.values(usage).map(Number), 1);
    const chart = el("div", { className: "ls-chart", "aria-label": "Engine usage" });
    const entries = Object.entries(usage);
    if (!entries.length) {
      chart.append(el("p", { className: "ls-field__hint", text: "No engine usage recorded yet." }));
    } else {
      entries.forEach(([name, count]) => {
        const h = Math.max(4, Math.round((Number(count) / maxUsage) * 100));
        chart.append(el("div", { className: "ls-chart__bar" }, [
          el("div", { className: "ls-chart__fill", style: `height:${h}%`, title: `${name}: ${count}` }),
          el("div", { className: "ls-chart__label", text: `${name}\n${count}` }),
        ]));
      });
    }

    host.append(
      el("div", { className: "ls-metric-row" }, [
        metricCard("Generations", totals.jobs),
        metricCard("Successful", totals.completed),
        metricCard("Failed", totals.failed),
        metricCard("READY assets", totals.ready),
        metricCard("Success rate", fmtRate(m.render_success_rate)),
        metricCard("Avg time to ready", fmtSec(m.avg_time_to_ready_sec)),
        metricCard("Avg GPU render", fmtSec(m.avg_gpu_render_duration_sec)),
        metricCard("Avg queue latency", fmtSec(m.avg_queue_latency_sec)),
        metricCard("QC failure rate", fmtRate(m.qc_failure_rate)),
      ]),
      el("section", { className: "ls-panel", style: "padding:1.15rem;margin-top:0.5rem" }, [
        el("div", { className: "ls-section-hd" }, [
          el("div", {}, [el("h2", { text: "Engine usage" }), el("p", { text: "Counts from stored jobs" })]),
        ]),
        chart,
      ]),
      el("section", { className: "ls-panel", style: "padding:1.15rem;margin-top:1rem" }, [
        el("h3", { text: "Cost breakdown (reported)" }),
        el("p", { className: "ls-field__hint", text: m.cost_breakdown?.note || "" }),
        el("div", { className: "ls-metric-row", style: "margin-top:0.75rem" }, [
          metricCard("Provider API USD", m.cost_breakdown?.provider_api_cost_usd ?? 0),
          metricCard("Estimated compute USD", m.cost_breakdown?.estimated_compute_cost_usd ?? 0),
          metricCard("Unknown compute jobs", m.cost_breakdown?.unknown_compute_cost_jobs ?? 0),
        ]),
      ]),
    );
  }

  await render();
  return store.subscribe((t) => { if (t === "data") render(); });
}

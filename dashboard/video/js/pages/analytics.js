/**
 * Analytics — real metrics only; empty when nulls.
 */

import { el, clear, emptyState, skeleton, formatDuration } from "../ui.js";

function formatMetric(n, { digits = 2, suffix = "", asPercent = false } = {}) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  let v = Number(n);
  if (asPercent) v *= 100;
  const s = Number.isInteger(v) ? String(v) : v.toFixed(digits);
  return `${s}${asPercent ? "%" : ""}${suffix}`;
}

function barChart(title, entries) {
  const wrap = el("figure", { className: "ls-chart", "aria-label": title });
  wrap.append(el("figcaption", { text: title }));
  if (!entries.length) {
    wrap.append(el("p", { className: "ls-field__hint", text: "No data" }));
    return wrap;
  }
  const max = Math.max(...entries.map((e) => e.value), 0.0001);
  const list = el("ul", { className: "ls-chart__bars" });
  entries.forEach((e) => {
    const pct = Math.round((e.value / max) * 100);
    list.append(el("li", { className: "ls-chart__row" }, [
      el("span", { className: "ls-chart__label", text: e.label }),
      el("span", {
        className: "ls-chart__track",
        role: "img",
        "aria-label": `${e.label}: ${e.value}`,
      }, [
        el("span", { className: "ls-chart__fill", style: `width:${pct}%` }),
      ]),
      el("span", { className: "ls-chart__value", text: String(e.display ?? e.value) }),
    ]));
  });
  wrap.append(list);
  return wrap;
}

function metricCard(label, value) {
  return el("div", { className: "ls-stat" }, [
    el("div", { className: "ls-stat__value", text: value }),
    el("div", { className: "ls-stat__label", text: label }),
  ]);
}

export async function renderAnalytics(root, ctx) {
  const { api, store, navigate } = ctx;
  clear(root);

  const page = el("div", { className: "ls-page" });
  page.append(el("header", { className: "ls-hero ls-hero--compact" }, [
    el("div", { className: "ls-hero__brand", text: "Lumen" }),
    el("h2", { className: "ls-hero__title", text: "Analytics" }),
    el("p", { className: "ls-hero__lede", text: "Stored gateway metrics only — nulls stay empty, never invented." }),
  ]));

  const body = el("div", { className: "ls-analytics" });
  body.append(skeleton());
  page.append(body);
  root.append(page);

  try {
    const m = api.getApiKey()
      ? await api.metrics()
      : store.metrics;
    if (m) store.metrics = m;

    if (!m) {
      body.replaceChildren(emptyState({
        title: "No metrics yet",
        body: api.getApiKey()
          ? "The gateway has not stored measurable outcomes."
          : "Set an API key to load stored metrics.",
        action: !api.getApiKey()
          ? el("button", { type: "button", className: "ls-btn", text: "Settings", onClick: () => navigate("settings") })
          : null,
      }));
      return () => {};
    }

    const rates = [
      ["Render success", formatMetric(m.render_success_rate, { asPercent: true, digits: 1 })],
      ["Provider failure", formatMetric(m.provider_failure_rate, { asPercent: true, digits: 1 })],
      ["QC failure", formatMetric(m.qc_failure_rate, { asPercent: true, digits: 1 })],
      ["Publish handoff", formatMetric(m.publish_handoff_reliability, { asPercent: true, digits: 1 })],
    ];
    const timings = [
      ["Time to first frame", m.avg_time_to_first_frame_sec != null ? formatDuration(m.avg_time_to_first_frame_sec) : "—"],
      ["Time to ready", m.avg_time_to_ready_sec != null ? formatDuration(m.avg_time_to_ready_sec) : "—"],
      ["GPU render", m.avg_gpu_render_duration_sec != null ? formatDuration(m.avg_gpu_render_duration_sec) : "—"],
      ["Queue latency", m.avg_queue_latency_sec != null ? formatDuration(m.avg_queue_latency_sec) : "—"],
    ];

    const usage = m.engine_usage && typeof m.engine_usage === "object" ? m.engine_usage : {};
    const totals = m.totals && typeof m.totals === "object" ? m.totals : {};
    const allNull =
      rates.every(([, v]) => v === "—") &&
      timings.every(([, v]) => v === "—") &&
      !Object.keys(usage).length &&
      !Object.keys(totals).length;

    body.replaceChildren();
    if (allNull) {
      body.append(emptyState({
        title: "No metrics yet",
        body: "Charts stay empty until real jobs complete and metrics are stored.",
      }));
      return () => {};
    }

    const rateRow = el("div", { className: "ls-stat-row" });
    rates.forEach(([label, value]) => rateRow.append(metricCard(label, value)));
    const timeRow = el("div", { className: "ls-stat-row" });
    timings.forEach(([label, value]) => timeRow.append(metricCard(label, value)));

    body.append(
      el("section", { className: "ls-panel" }, [el("h3", { text: "Rates" }), rateRow]),
      el("section", { className: "ls-panel" }, [el("h3", { text: "Timing averages" }), timeRow])
    );

    if (m.avg_cost_per_usable_asset_usd != null) {
      body.append(el("section", { className: "ls-panel" }, [
        el("h3", { text: "Cost" }),
        metricCard("Avg USD / usable asset", formatMetric(m.avg_cost_per_usable_asset_usd, { digits: 4 })),
      ]));
    }

    const usageEntries = Object.entries(usage)
      .filter(([, v]) => v != null)
      .map(([label, value]) => ({ label, value: Number(value), display: String(value) }));
    body.append(el("section", { className: "ls-panel" }, [
      usageEntries.length
        ? barChart("Engine usage", usageEntries)
        : emptyState({ title: "No engine usage", body: "Usage appears after completed renders." }),
    ]));

    const totalEntries = Object.entries(totals)
      .filter(([, v]) => v != null)
      .map(([label, value]) => ({ label, value: Number(value), display: String(value) }));
    if (totalEntries.length) {
      body.append(el("section", { className: "ls-panel" }, [barChart("Totals", totalEntries)]));
    }

    const cost = m.cost_breakdown && typeof m.cost_breakdown === "object" ? m.cost_breakdown : null;
    if (cost) {
      const costEntries = Object.entries(cost)
        .filter(([, v]) => typeof v === "number")
        .map(([label, value]) => ({
          label,
          value,
          display: formatMetric(value, { digits: 3 }),
        }));
      if (costEntries.length) {
        body.append(el("section", { className: "ls-panel" }, [barChart("Cost breakdown", costEntries)]));
      }
    }
  } catch (err) {
    body.replaceChildren(emptyState({ title: "Metrics unavailable", body: err.message || "Error" }));
  }

  return () => {};
}

export default renderAnalytics;

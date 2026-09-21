import { prefersReducedMotion } from "./motion.js";

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "className") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const child of [].concat(children)) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

/** Alias used by some page modules */
export const clearRoot = clear;

export function emptyState({ title, body, action = null, icon = "◇" }) {
  const wrap = el("div", { className: "ls-empty" }, [
    el("div", { className: "ls-empty__icon", text: icon }),
    el("h3", { text: title }),
    el("p", { text: body }),
  ]);
  if (action) wrap.append(action);
  return wrap;
}

export function skeleton(kind = "block") {
  if (kind === "gallery") {
    return el("div", { className: "ls-grid" }, Array.from({ length: 6 }, () =>
      el("div", {}, [
        el("div", { className: "ls-skeleton ls-skeleton--media" }),
        el("div", { className: "ls-skeleton ls-skeleton--text", style: "width:70%;margin-top:0.75rem" }),
        el("div", { className: "ls-skeleton ls-skeleton--text", style: "width:40%" }),
      ])
    ));
  }
  return el("div", {}, [
    el("div", { className: "ls-skeleton ls-skeleton--title" }),
    el("div", { className: "ls-skeleton ls-skeleton--text" }),
    el("div", { className: "ls-skeleton ls-skeleton--text", style: "width:80%" }),
    el("div", { className: "ls-skeleton ls-skeleton--media", style: "margin-top:1rem" }),
  ]);
}

const STATUS_TONE = {
  COMPLETED: "ok",
  READY: "ok",
  AVAILABLE: "ok",
  QUEUED: "info",
  PLANNING: "info",
  ROUTING: "info",
  DOWNLOADING_MODEL: "warn",
  RENDERING: "accent",
  STITCHING: "accent",
  AUDIO: "accent",
  QC: "warn",
  FAILED: "bad",
  CANCELLED: "warn",
  MODEL_NOT_INSTALLED: "warn",
  GPU_UNAVAILABLE: "bad",
  UNHEALTHY: "bad",
  DISABLED: "warn",
};

export function statusBadge(status) {
  const s = String(status || "unknown");
  const tone = STATUS_TONE[s] || "info";
  return el("span", {
    className: `ls-badge ls-badge--${tone}`,
    text: s.replaceAll("_", " "),
  });
}

export function abridgeId(id, size = 8) {
  if (!id) return "—";
  const s = String(id);
  return s.length <= size + 2 ? s : `${s.slice(0, size)}…`;
}

export function formatDuration(sec) {
  if (sec == null || Number.isNaN(Number(sec))) return "—";
  const n = Math.max(0, Number(sec));
  const m = Math.floor(n / 60);
  const s = Math.floor(n % 60);
  return m > 0 ? `${m}:${String(s).padStart(2, "0")}` : `${s}s`;
}

export function formatClock(sec) {
  if (sec == null || Number.isNaN(Number(sec))) return "0:00";
  const n = Math.max(0, Number(sec));
  const m = Math.floor(n / 60);
  const s = Math.floor(n % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function formatRelative(iso) {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const diff = Date.now() - t;
  const sec = Math.round(diff / 1000);
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return new Date(t).toLocaleString();
}

export function formatElapsed(startIso, endIso = null) {
  if (!startIso) return "—";
  const start = new Date(startIso).getTime();
  if (Number.isNaN(start)) return "—";
  const end = endIso ? new Date(endIso).getTime() : Date.now();
  const sec = Math.max(0, Math.floor((end - start) / 1000));
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function projectTitle(job) {
  const p = (job?.prompt || "").trim();
  if (!p) return `Project ${abridgeId(job?.job_id, 6)}`;
  return p.length > 72 ? `${p.slice(0, 72)}…` : p;
}

export function engineCapabilities(providers = []) {
  const byName = Object.fromEntries((providers || []).map((p) => [p.name, p]));
  const tasksOf = (name) => byName[name]?.tasks || [];
  const readyI2V = (providers || []).filter(
    (p) => p.ready && (p.tasks || []).includes("image-to-video")
  );
  const readyT2V = (providers || []).filter(
    (p) => p.ready && (p.tasks || []).includes("text-to-video")
  );
  return {
    byName,
    tasksOf,
    readyI2V,
    readyT2V,
    anyReady: (providers || []).some((p) => p.ready),
    supportsI2V: readyI2V.length > 0,
    supportsT2V: readyT2V.length > 0,
  };
}

export function sanitizeFailure(job) {
  return {
    jobId: job?.job_id || null,
    engine: job?.engine || null,
    category: job?.failure_category || null,
    reason: job?.failure_reason || "Generation failed",
    timestamp: job?.completed_at || job?.created_at || null,
  };
}

export function downloadBlobFile(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function field(label, control, hint) {
  const wrap = el("label", { className: "ls-field" }, [
    el("span", { className: "ls-field__label", text: label }),
    control,
  ]);
  if (hint) wrap.append(el("span", { className: "ls-field__hint", text: hint }));
  return wrap;
}

export function select(name, options, value) {
  const s = el("select", { className: "ls-select", name });
  for (const opt of options) {
    const o = el("option", {
      value: opt.value,
      text: opt.label,
      selected: opt.value === value ? true : undefined,
      disabled: opt.disabled || undefined,
    });
    s.append(o);
  }
  return s;
}

export { prefersReducedMotion };

export default {
  el,
  clear,
  clearRoot,
  emptyState,
  skeleton,
  statusBadge,
  abridgeId,
  formatDuration,
  formatClock,
  formatRelative,
  formatElapsed,
  projectTitle,
  engineCapabilities,
  sanitizeFailure,
  downloadBlobFile,
  field,
  select,
};

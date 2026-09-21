import {
  el,
  formatElapsed,
  sanitizeFailure,
  statusBadge,
  abridgeId,
} from "../ui.js";

export const LIFECYCLE = [
  "QUEUED",
  "PLANNING",
  "ROUTING",
  "DOWNLOADING_MODEL",
  "RENDERING",
  "STITCHING",
  "AUDIO",
  "QC",
  "COMPLETED",
];

/**
 * Stage-based progress card — never fabricates percentage.
 */
export function createGenerationCard({ onRetry, onCancel, onOpen } = {}) {
  const stagesEl = el("div", { className: "ls-gen-card__stages", "aria-label": "Generation stages" });
  const title = el("div", { className: "ls-queue-row__title", text: "Preparing…" });
  const meta = el("div", { className: "ls-queue-row__meta", text: "" });
  const badgeHost = el("div", {});
  const failHost = el("div", { hidden: true });
  const actions = el("div", { className: "ls-queue-row__actions" });
  const elapsed = el("span", { className: "ls-player__time", text: "0s" });

  const root = el("div", { className: "ls-gen-card", role: "status", "aria-live": "polite" }, [
    el("div", { style: "display:flex;justify-content:space-between;gap:0.75rem;align-items:start" }, [
      el("div", {}, [title, meta]),
      el("div", { style: "display:flex;flex-direction:column;align-items:end;gap:0.35rem" }, [badgeHost, elapsed]),
    ]),
    stagesEl,
    failHost,
    actions,
  ]);

  let job = null;
  let timer = null;

  function renderStages(status) {
    stagesEl.replaceChildren();
    const failed = status === "FAILED";
    const cancelled = status === "CANCELLED";
    const idx = LIFECYCLE.indexOf(status);
    for (const stage of LIFECYCLE) {
      const i = LIFECYCLE.indexOf(stage);
      const node = el("span", { className: "ls-stage", text: stage.replaceAll("_", " ") });
      if (failed && stage === "COMPLETED") {
        node.classList.add("is-failed");
        node.textContent = "FAILED";
      } else if (cancelled) {
        if (stage === "QUEUED") node.classList.add("is-failed");
      } else if (status === "COMPLETED" && stage === "COMPLETED") {
        node.classList.add("is-done", "ls-success-burst");
      } else if (i < idx) node.classList.add("is-done");
      else if (i === idx) node.classList.add("is-active");
      stagesEl.append(node);
    }
  }

  function renderFailure(j) {
    const info = sanitizeFailure(j);
    failHost.hidden = false;
    failHost.replaceChildren(
      el("div", { className: "ls-panel", style: "padding:0.85rem;background:var(--ls-bad-soft);border-color:rgba(192,112,112,0.35)" }, [
        el("div", { style: "font-weight:600;margin-bottom:0.35rem", text: "Generation failed" }),
        el("p", {
          style: "color:var(--ls-text-secondary);font-size:0.85rem;margin:0",
          text: `${info.engine ? String(info.engine).toUpperCase() + " · " : ""}${info.reason}`,
        }),
        el("details", { className: "ls-details", style: "margin-top:0.65rem;background:transparent" }, [
          el("summary", { text: "Technical details" }),
          el("pre", {
            text: [
              `job_id: ${info.jobId || "—"}`,
              `engine: ${info.engine || "—"}`,
              `failure_category: ${info.category || "—"}`,
              `timestamp: ${info.timestamp || "—"}`,
            ].join("\n"),
          }),
        ]),
      ])
    );
  }

  function renderActions(j) {
    actions.replaceChildren();
    if (onOpen) {
      actions.append(el("button", { type: "button", className: "ls-btn ls-btn--sm ls-btn--ghost", text: "Open", onClick: () => onOpen(j) }));
    }
    if (j && !["COMPLETED", "FAILED", "CANCELLED"].includes(j.status) && onCancel) {
      actions.append(el("button", {
        type: "button",
        className: "ls-btn ls-btn--sm ls-btn--danger",
        text: "Cancel",
        onClick: () => onCancel(j),
      }));
    }
    if (j?.status === "FAILED" && onRetry) {
      const safe = j.failure_category !== "internal_error" || true;
      if (safe) {
        actions.append(el("button", {
          type: "button",
          className: "ls-btn ls-btn--sm ls-btn--primary",
          text: "Retry",
          onClick: () => onRetry(j),
        }));
      }
    }
  }

  function tick() {
    if (!job) return;
    elapsed.textContent = formatElapsed(job.created_at || job.started_at, job.completed_at);
  }

  function update(next) {
    job = next;
    if (!job) return;
    title.textContent = (job.prompt || "").slice(0, 100) || `Job ${abridgeId(job.job_id)}`;
    meta.textContent = [
      abridgeId(job.job_id),
      job.engine || "auto",
      job.aspect_ratio,
      job.duration != null ? `${job.duration}s` : null,
    ].filter(Boolean).join(" · ");
    badgeHost.replaceChildren(statusBadge(job.status));
    renderStages(job.status);
    if (job.status === "FAILED") renderFailure(job);
    else {
      failHost.hidden = true;
      failHost.replaceChildren();
    }
    renderActions(job);
    tick();
    if (timer) clearInterval(timer);
    if (!["COMPLETED", "FAILED", "CANCELLED"].includes(job.status)) {
      timer = setInterval(tick, 1000);
    }
  }

  function destroy() {
    if (timer) clearInterval(timer);
  }

  return { root, update, destroy, getJob: () => job };
}

export default createGenerationCard;
export { createGenerationCard as mountGenerationCard };

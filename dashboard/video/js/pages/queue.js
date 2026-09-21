/**
 * Operational generation queue.
 */

import {
  el,
  clear,
  projectTitle,
  statusBadge,
  formatElapsed,
  formatRelative,
  abridgeId,
  emptyState,
  skeleton,
} from "../ui.js";
import { toast } from "../components/toast.js";

function bucket(status, store) {
  const s = String(status || "").toUpperCase();
  if (s === "QUEUED") return "QUEUED";
  if (store.isActive(s) && s !== "QUEUED") return "ACTIVE";
  if (s === "COMPLETED") return "COMPLETED";
  if (s === "FAILED" || s === "CANCELLED") return "FAILED";
  return "OTHER";
}

export async function renderQueue(root, ctx) {
  const { api, store, navigate } = ctx;
  clear(root);

  let filter = "all";
  let timer = null;
  const thumbs = {};

  const page = el("div", { className: "ls-page" });
  page.append(el("header", { className: "ls-hero ls-hero--compact" }, [
    el("div", { className: "ls-hero__brand", text: "Lumen" }),
    el("h2", { className: "ls-hero__title", text: "Queue" }),
    el("p", { className: "ls-hero__lede", text: "Live jobs — cancel non-terminal work; never invent progress." }),
  ]));

  const filterSel = el("select", { className: "ls-select", "aria-label": "Queue filter" }, [
    el("option", { value: "all", text: "All" }),
    el("option", { value: "QUEUED", text: "Queued" }),
    el("option", { value: "ACTIVE", text: "Active" }),
    el("option", { value: "COMPLETED", text: "Completed" }),
    el("option", { value: "FAILED", text: "Failed / cancelled" }),
  ]);
  const refreshBtn = el("button", { type: "button", className: "ls-btn", text: "Refresh" });
  const autoCb = el("input", { type: "checkbox", checked: true, id: "ls-queue-auto" });
  page.append(el("div", { className: "ls-toolbar" }, [
    filterSel,
    refreshBtn,
    el("label", { className: "ls-field ls-field--check" }, [autoCb, el("span", { text: "Auto-refresh" })]),
  ]));

  const summary = el("div", { className: "ls-stat-row", "aria-live": "polite" });
  const tableWrap = el("div", { className: "ls-table-wrap" });
  tableWrap.append(skeleton());
  page.append(summary, tableWrap);
  root.append(page);

  function jobs() {
    return store.jobs || [];
  }

  function paintSummary() {
    const counts = { QUEUED: 0, ACTIVE: 0, COMPLETED: 0, FAILED: 0 };
    jobs().forEach((j) => {
      const b = bucket(j.status, store);
      if (counts[b] != null) counts[b] += 1;
    });
    summary.replaceChildren(
      ...Object.entries(counts).map(([k, v]) =>
        el("div", { className: "ls-stat" }, [
          el("div", { className: "ls-stat__value", text: String(v) }),
          el("div", { className: "ls-stat__label", text: k }),
        ])
      )
    );
  }

  function rows() {
    if (filter === "all") return jobs();
    return jobs().filter((j) => bucket(j.status, store) === filter);
  }

  function paintTable() {
    const list = rows();
    tableWrap.replaceChildren();
    if (!list.length) {
      tableWrap.append(emptyState({ title: "Queue empty", body: "No jobs match this filter." }));
      return;
    }

    const table = el("table", { className: "ls-table" });
    table.append(el("thead", {}, [
      el("tr", {}, [
        el("th", { text: "Job" }),
        el("th", { text: "Thumb" }),
        el("th", { text: "Engine" }),
        el("th", { text: "Status" }),
        el("th", { text: "Created" }),
        el("th", { text: "Elapsed" }),
        el("th", { text: "Detail" }),
        el("th", { text: "Actions" }),
      ]),
    ]));
    const tbody = el("tbody");
    list.forEach((j) => {
      const thumbCell = el("td", { className: "ls-table__thumb" });
      const img = el("img", { alt: "", hidden: true, width: "48", height: "48" });
      thumbCell.append(img);

      const detail =
        String(j.status).toUpperCase() === "FAILED"
          ? String(j.failure_reason || j.failure_category || "Failed").slice(0, 120)
          : projectTitle(j).slice(0, 48);

      const cancelBtn = el("button", {
        type: "button",
        className: "ls-btn ls-btn--sm ls-btn--danger",
        text: "Cancel",
        disabled: store.isTerminal(j.status) || undefined,
        title: store.isTerminal(j.status) ? "Job already terminal" : "Cancel job",
      });
      cancelBtn.addEventListener("click", async () => {
        if (store.isTerminal(j.status)) return;
        cancelBtn.disabled = true;
        try {
          await api.cancel(j.job_id);
          toast(`Cancel ${abridgeId(j.job_id)}`, { type: "info" });
          await refresh();
        } catch (e) {
          toast(e.message || "Cancel failed", { type: "error" });
          cancelBtn.disabled = false;
        }
      });

      tbody.append(el("tr", { className: `ls-table__row--${bucket(j.status, store).toLowerCase()}` }, [
        el("td", {}, [
          el("button", {
            type: "button",
            className: "ls-btn ls-btn--ghost ls-btn--sm",
            text: abridgeId(j.job_id),
            onClick: () => navigate("projects", [j.job_id]),
          }),
          el("div", { className: "ls-field__hint", text: projectTitle(j).slice(0, 36) }),
        ]),
        thumbCell,
        el("td", { text: j.engine || "—" }),
        el("td", {}, [statusBadge(j.status)]),
        el("td", { text: formatRelative(j.created_at) }),
        el("td", { text: formatElapsed(j.started_at || j.created_at, j.completed_at) }),
        el("td", { text: detail }),
        el("td", {}, [cancelBtn]),
      ]));

      if (j.ready && api.thumbnailBlob) {
        if (thumbs[j.job_id]) {
          img.src = thumbs[j.job_id];
          img.hidden = false;
        } else {
          api.thumbnailBlob(j.job_id).then((blob) => {
            const url = URL.createObjectURL(blob);
            thumbs[j.job_id] = url;
            img.src = url;
            img.hidden = false;
          }).catch(() => {});
        }
      }
    });
    table.append(tbody);
    tableWrap.append(table);
  }

  async function refresh() {
    try {
      const data = await api.jobs({ limit: 100 });
      store.jobs = data?.jobs || [];
      store.emit("data");
    } catch (err) {
      tableWrap.replaceChildren(emptyState({ title: "Queue unavailable", body: err.message || "Error" }));
      return;
    }
    paintSummary();
    paintTable();
  }

  function syncAuto() {
    if (timer) { clearInterval(timer); timer = null; }
    if (autoCb.checked) timer = setInterval(() => refresh(), 4000);
  }

  filterSel.addEventListener("change", () => { filter = filterSel.value; paintTable(); });
  refreshBtn.addEventListener("click", () => refresh());
  autoCb.addEventListener("change", syncAuto);

  paintSummary();
  paintTable();
  await refresh();
  syncAuto();

  const unsub = store.subscribe((type) => {
    if (type === "data" || type === "job") {
      paintSummary();
      paintTable();
    }
  });

  return () => {
    unsub();
    if (timer) clearInterval(timer);
    Object.values(thumbs).forEach((u) => {
      try { URL.revokeObjectURL(u); } catch { /* ignore */ }
    });
  };
}

export default renderQueue;

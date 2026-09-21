/**
 * Projects gallery — grid/list, search/filter/sort, cinematic detail.
 */

import {
  el,
  clear,
  projectTitle,
  statusBadge,
  formatDuration,
  formatRelative,
  abridgeId,
  emptyState,
  skeleton,
  downloadBlobFile,
} from "../ui.js";
import { stagger } from "../motion.js";
import { createVideoPlayer } from "../components/videoPlayer.js";
import { toast } from "../components/toast.js";
import { parseRoute } from "../router.js";

function newIdempotencyKey(prefix = "proj") {
  return `${prefix}-${crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
}

export async function renderProjects(root, ctx) {
  const { api, store, navigate } = ctx;
  clear(root);

  let view = "grid";
  let query = "";
  let statusFilter = "all";
  let sort = "newest";
  /** @type {Record<string, string>} */
  const thumbs = {};
  let overlay = null;
  let player = null;

  const page = el("div", { className: "ls-page" });
  page.append(el("header", { className: "ls-hero ls-hero--compact" }, [
    el("div", { className: "ls-hero__brand", text: "Lumen" }),
    el("h2", { className: "ls-hero__title", text: "Projects" }),
    el("p", { className: "ls-hero__lede", text: "Every generation job as a project — real status only." }),
  ]));

  const search = el("input", {
    type: "search",
    className: "ls-input",
    placeholder: "Search prompts or job id…",
    "aria-label": "Search projects",
  });
  const statusSel = el("select", { className: "ls-select", "aria-label": "Filter status" }, [
    el("option", { value: "all", text: "All statuses" }),
    ...["QUEUED", "RENDERING", "COMPLETED", "FAILED", "CANCELLED"].map((s) =>
      el("option", { value: s, text: s })
    ),
    el("option", { value: "ACTIVE", text: "Active pipeline" }),
  ]);
  const sortSel = el("select", { className: "ls-select", "aria-label": "Sort" }, [
    el("option", { value: "newest", text: "Newest" }),
    el("option", { value: "oldest", text: "Oldest" }),
    el("option", { value: "status", text: "Status" }),
  ]);
  const gridBtn = el("button", { type: "button", className: "ls-btn ls-btn--sm is-active", "aria-pressed": "true", text: "Grid" });
  const listBtn = el("button", { type: "button", className: "ls-btn ls-btn--sm", "aria-pressed": "false", text: "List" });

  page.append(el("div", { className: "ls-toolbar", role: "search" }, [
    search, statusSel, sortSel, gridBtn, listBtn,
  ]));

  const gallery = el("div", { className: "ls-grid", "aria-live": "polite" });
  gallery.append(skeleton("gallery"));
  const detailHost = el("div", {});
  page.append(gallery, detailHost);
  root.append(page);

  function jobs() {
    return store.jobs || [];
  }

  function filtered() {
    let rows = [...jobs()];
    if (query) {
      const q = query.toLowerCase();
      rows = rows.filter(
        (j) =>
          String(j.prompt || "").toLowerCase().includes(q) ||
          String(j.job_id || "").toLowerCase().includes(q) ||
          String(j.engine || "").toLowerCase().includes(q)
      );
    }
    if (statusFilter === "ACTIVE") {
      rows = rows.filter((j) => store.isActive(j.status) && j.status !== "QUEUED");
    } else if (statusFilter !== "all") {
      rows = rows.filter((j) => j.status === statusFilter);
    }
    rows.sort((a, b) => {
      if (sort === "oldest") return new Date(a.created_at || 0) - new Date(b.created_at || 0);
      if (sort === "status") return String(a.status).localeCompare(String(b.status));
      return new Date(b.created_at || 0) - new Date(a.created_at || 0);
    });
    return rows;
  }

  async function ensureThumb(job, host) {
    if (!job?.ready || !job.job_id || !api.thumbnailBlob) return;
    if (thumbs[job.job_id]) {
      host.replaceChildren(el("img", { src: thumbs[job.job_id], alt: "" }));
      return;
    }
    try {
      const blob = await api.thumbnailBlob(job.job_id);
      const url = URL.createObjectURL(blob);
      thumbs[job.job_id] = url;
      host.replaceChildren(el("img", { src: url, alt: "" }));
    } catch {
      /* no thumb */
    }
  }

  function paint() {
    const rows = filtered();
    gallery.className = view === "grid" ? "ls-grid" : "ls-queue-list";
    gallery.replaceChildren();
    if (!rows.length) {
      gallery.append(emptyState({
        title: jobs().length ? "No matches" : "No projects yet",
        body: jobs().length ? "Try another search or filter." : "Create a generation to populate this gallery.",
        action: jobs().length
          ? null
          : el("button", { type: "button", className: "ls-btn ls-btn--primary", text: "Create", onClick: () => navigate("create") }),
      }));
      return;
    }
    rows.forEach((j) => {
      const thumb = el("div", { className: "ls-media-card__thumb" }, [
        el("div", { className: "ls-media-card__thumb-fallback", text: j.aspect_ratio || "▣" }),
      ]);
      const card = el("button", {
        type: "button",
        className: view === "grid" ? "ls-media-card" : "ls-queue-row",
        "aria-label": `Open ${abridgeId(j.job_id)}`,
        onClick: () => openDetail(j),
      }, view === "grid"
        ? [
            thumb,
            el("div", { className: "ls-media-card__body" }, [
              el("div", { className: "ls-media-card__title", text: projectTitle(j) }),
              el("div", { className: "ls-media-card__meta" }, [
                statusBadge(j.status),
                el("span", { text: j.engine || "—" }),
                el("span", { text: formatDuration(j.output_duration ?? j.duration) }),
                el("span", { text: j.aspect_ratio || "—" }),
                el("span", { text: formatRelative(j.created_at) }),
              ]),
            ]),
          ]
        : [
            thumb,
            el("div", {}, [
              el("div", { className: "ls-queue-row__title", text: projectTitle(j) }),
              el("div", { className: "ls-queue-row__meta", text: `${j.engine || "—"} · ${formatRelative(j.created_at)}` }),
            ]),
            statusBadge(j.status),
          ]);
      gallery.append(card);
      ensureThumb(j, thumb);
    });
    stagger(gallery);
  }

  function closeDetail() {
    player?.destroy();
    player = null;
    overlay?.remove();
    overlay = null;
  }

  async function openDetail(jobSeed) {
    closeDetail();
    overlay = el("div", {
      className: "ls-detail-overlay",
      role: "dialog",
      "aria-modal": "true",
      "aria-label": "Project detail",
      onClick: (e) => { if (e.target === overlay) closeDetail(); },
    });
    const panel = el("div", { className: "ls-panel ls-detail-panel" });
    const closeBtn = el("button", { type: "button", className: "ls-btn", text: "Close", onClick: closeDetail });
    panel.append(el("header", { style: "display:flex;justify-content:space-between;gap:1rem;align-items:start" }, [
      el("h2", { text: projectTitle(jobSeed) }),
      closeBtn,
    ]));
    overlay.append(panel);
    detailHost.append(overlay);

    const onKey = (e) => { if (e.key === "Escape") closeDetail(); };
    document.addEventListener("keydown", onKey);
    const prevClose = closeDetail;
    // wrap once
    const cleanupKey = () => document.removeEventListener("keydown", onKey);

    let job = jobSeed;
    try {
      job = await api.getVideo(jobSeed.job_id);
    } catch {
      /* seed */
    }

    player = createVideoPlayer({
      aspectRatio: job.aspect_ratio || "16:9",
      onDownload: job.ready
        ? async (id) => {
            const blob = await api.downloadBlob(id);
            downloadBlobFile(blob, `${id}.mp4`);
          }
        : undefined,
      onRegenerate: async () => {
        try {
          const created = await api.createVideo({
            prompt: job.prompt,
            duration: job.duration,
            aspect_ratio: job.aspect_ratio,
            engine: job.engine || "auto",
            idempotency_key: newIdempotencyKey("regen"),
          });
          toast(`Regenerated ${abridgeId(created.job_id)}`, { type: "success" });
          store.trackJob(created.job_id);
          cleanupKey();
          prevClose();
          navigate("queue");
        } catch (e) {
          toast(e.message || "Failed", { type: "error" });
        }
      },
      onDuplicate: () => {
        store.createPrefill = {
          prompt: job.prompt,
          aspect_ratio: job.aspect_ratio,
          duration: job.duration,
        };
        cleanupKey();
        prevClose();
        navigate("create");
      },
      onVariation: async () => {
        try {
          const created = await api.createVideo({
            prompt: job.prompt,
            duration: job.duration,
            aspect_ratio: job.aspect_ratio,
            engine: job.engine || "auto",
            seed: Math.floor(Math.random() * 1e9),
            idempotency_key: newIdempotencyKey("var"),
          });
          toast(`Variation ${abridgeId(created.job_id)}`, { type: "success" });
          store.trackJob(created.job_id);
        } catch (e) {
          toast(e.message || "Failed", { type: "error" });
        }
      },
    });

    panel.append(
      player.root,
      el("div", {}, [
        statusBadge(job.status),
        el("p", { text: `ID: ${job.job_id}` }),
        el("p", { text: `Model: ${job.engine || "—"} · ${job.model || "—"}` }),
        el("p", { text: `Duration: ${formatDuration(job.output_duration ?? job.duration)} · ${job.aspect_ratio || "—"} · ${job.resolution || "—"}` }),
        el("p", { text: `Created: ${formatRelative(job.created_at)}` }),
        job.failure_reason
          ? el("p", { style: "color:var(--ls-bad)", text: String(job.failure_reason).slice(0, 400) })
          : null,
        job.ready
          ? null
          : el("p", { className: "ls-field__hint", text: "Player enabled only when the asset is READY." }),
      ])
    );

    if (job.ready) {
      try {
        const blob = await api.downloadBlob(job.job_id);
        await player.loadBlob(blob, { id: job.job_id, ratio: job.aspect_ratio });
      } catch (e) {
        panel.append(el("p", { className: "ls-field__hint", text: e.message || "Preview unavailable" }));
      }
    }

    // stash cleanup on overlay removal
    overlay._cleanupKey = cleanupKey;
  }

  search.addEventListener("input", () => { query = search.value.trim(); paint(); });
  statusSel.addEventListener("change", () => { statusFilter = statusSel.value; paint(); });
  sortSel.addEventListener("change", () => { sort = sortSel.value; paint(); });
  gridBtn.addEventListener("click", () => {
    view = "grid";
    gridBtn.classList.add("is-active");
    listBtn.classList.remove("is-active");
    paint();
  });
  listBtn.addEventListener("click", () => {
    view = "list";
    listBtn.classList.add("is-active");
    gridBtn.classList.remove("is-active");
    paint();
  });

  paint();
  const { parts } = parseRoute();
  if (parts[0]) {
    const j = jobs().find((x) => x.job_id === parts[0]);
    if (j) openDetail(j);
  }

  const unsub = store.subscribe((type) => {
    if (type === "data" || type === "job") paint();
  });

  return () => {
    unsub();
    closeDetail();
    Object.values(thumbs).forEach((u) => {
      try { URL.revokeObjectURL(u); } catch { /* ignore */ }
    });
  };
}

export default renderProjects;

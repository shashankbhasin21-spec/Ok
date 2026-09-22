/**
 * Asset library — uploads + READY job outputs.
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
import { toast } from "../components/toast.js";

export async function renderAssets(root, ctx) {
  const { api, store, navigate } = ctx;
  clear(root);

  /** @type {Array<Record<string, unknown>>} */
  let items = [];
  let query = "";
  let kind = "all";
  const urls = {};

  const page = el("div", { className: "ls-page" });
  page.append(el("header", { className: "ls-hero ls-hero--compact" }, [
    el("div", { className: "ls-hero__brand", text: "Lumen" }),
    el("h2", { className: "ls-hero__title", text: "Assets" }),
    el("p", { className: "ls-hero__lede", text: "Uploads and READY outputs — reuse into Create without inventing paths." }),
  ]));

  const search = el("input", {
    type: "search", className: "ls-input", placeholder: "Search assets…", "aria-label": "Search",
  });
  const kindSel = el("select", { className: "ls-select", "aria-label": "Filter kind" }, [
    el("option", { value: "all", text: "All" }),
    el("option", { value: "upload", text: "Uploads" }),
    el("option", { value: "output", text: "READY outputs" }),
  ]);
  const refreshBtn = el("button", { type: "button", className: "ls-btn", text: "Refresh" });
  page.append(el("div", { className: "ls-toolbar" }, [search, kindSel, refreshBtn]));

  const preview = el("section", { className: "ls-panel", hidden: true, "aria-label": "Preview" });
  const previewBody = el("div", {});
  preview.append(el("h3", { text: "Preview" }), previewBody);

  const grid = el("div", { className: "ls-grid", "aria-live": "polite" });
  grid.append(skeleton("gallery"));
  page.append(preview, grid);
  root.append(page);

  function filtered() {
    let rows = items;
    if (kind !== "all") rows = rows.filter((a) => a.kind === kind);
    if (query) {
      const q = query.toLowerCase();
      rows = rows.filter(
        (a) =>
          String(a.name || "").toLowerCase().includes(q) ||
          String(a.id || "").toLowerCase().includes(q) ||
          String(a.path || "").toLowerCase().includes(q)
      );
    }
    return rows;
  }

  function showPreview(asset) {
    preview.hidden = false;
    previewBody.replaceChildren();
    const media = el("div", { className: "ls-assets-preview__media" });
    previewBody.append(
      media,
      el("p", {}, [el("strong", { text: asset.name || abridgeId(asset.id) })]),
      el("p", { className: "ls-field__hint", text: `${asset.kind} · ${formatRelative(asset.created_at)}` })
    );

    const dl = el("button", {
      type: "button",
      className: "ls-btn",
      text: "Download",
      disabled: !asset.downloadable || undefined,
      title: asset.downloadable ? "Download" : "Download only for READY video outputs",
    });
    const reuse = el("button", {
      type: "button",
      className: "ls-btn ls-btn--primary",
      text: "Reuse in Create",
      disabled: !asset.path || undefined,
      title: asset.path ? "Reuse in Create" : "No reusable path",
    });

    dl.addEventListener("click", async () => {
      if (!asset.downloadable || !asset.job_id) return;
      try {
        const blob = await api.downloadBlob(asset.job_id);
        downloadBlobFile(blob, `${asset.job_id}.mp4`);
      } catch (e) {
        toast(e.message || "Download failed", { type: "error" });
      }
    });
    reuse.addEventListener("click", () => {
      if (!asset.path) return;
      store.createPrefill = { input_image: asset.path, prompt: asset.prompt || "" };
      navigate("create");
    });
    previewBody.append(el("div", { className: "ls-toolbar" }, [dl, reuse]));

    (async () => {
      if (asset.kind === "output" && asset.job_id && asset.ready) {
        try {
          if (api.previewBlob) {
            const blob = await api.previewBlob(asset.job_id);
            const url = URL.createObjectURL(blob);
            urls[`p-${asset.id}`] = url;
            media.append(el("video", { className: "ls-assets-preview__video", src: url, controls: true, playsinline: true }));
            return;
          }
        } catch { /* fall through */ }
        try {
          const blob = await api.thumbnailBlob(asset.job_id);
          const url = URL.createObjectURL(blob);
          urls[`t-${asset.id}`] = url;
          media.append(el("img", { src: url, alt: "Thumbnail" }));
          return;
        } catch { /* fall through */ }
      }
      if (asset.kind === "upload" && asset.id && api.assetFileBlob) {
        try {
          const blob = await api.assetFileBlob(asset.id);
          const url = URL.createObjectURL(blob);
          urls[`u-${asset.id}`] = url;
          media.append(el("img", { src: url, alt: "Asset" }));
          return;
        } catch { /* fall through */ }
      }
      media.append(el("p", { className: "ls-field__hint", text: "No preview available" }));
    })();
  }

  function paint() {
    const rows = filtered();
    grid.replaceChildren();
    if (!rows.length) {
      grid.append(emptyState({
        title: items.length ? "No matches" : "No assets yet",
        body: items.length
          ? "Adjust search or filter."
          : "Upload from Image→Video / Create, or complete a READY generation.",
      }));
      return;
    }
    rows.forEach((asset) => {
      const thumb = el("div", { className: "ls-media-card__thumb" });
      const card = el("button", {
        type: "button",
        className: "ls-media-card",
        "aria-label": `Asset ${asset.name || asset.id}`,
        onClick: () => showPreview(asset),
      }, [
        thumb,
        el("div", { className: "ls-media-card__body" }, [
          el("div", { className: "ls-media-card__title", text: asset.name || abridgeId(asset.id) }),
          el("div", { className: "ls-media-card__meta" }, [
            statusBadge(asset.kind === "output" ? (asset.ready ? "READY" : asset.status) : "UPLOAD"),
            el("span", { text: formatRelative(asset.created_at) }),
            asset.duration != null ? el("span", { text: formatDuration(asset.duration) }) : null,
          ]),
        ]),
      ]);
      grid.append(card);
      if (asset.job_id && asset.ready && api.thumbnailBlob) {
        api.thumbnailBlob(asset.job_id).then((blob) => {
          const url = URL.createObjectURL(blob);
          urls[`g-${asset.id}`] = url;
          thumb.append(el("img", { src: url, alt: "" }));
        }).catch(() => {});
      }
    });
    stagger(grid);
  }

  async function load() {
    grid.replaceChildren(skeleton("gallery"));
    items = [];
    try {
      if (api.listAssets) {
        const listed = await api.listAssets();
        const arr = Array.isArray(listed) ? listed : listed?.assets || [];
        store.assets = arr;
        arr.forEach((a, i) => {
          items.push({
            id: a.id || a.path || `upload-${i}`,
            name: a.name || a.filename || abridgeId(a.id || a.path),
            path: a.path || a.url || a.id,
            kind: "upload",
            created_at: a.created_at,
            downloadable: false,
          });
        });
      }
    } catch (err) {
      toast(err.message || "listAssets failed", { type: "error", title: "Assets" });
    }

    try {
      const data = await api.jobs({ limit: 100 });
      const jobList = data?.jobs || store.jobs || [];
      jobList
        .filter((j) => j.ready || j.status === "COMPLETED")
        .forEach((j) => {
          items.push({
            id: j.asset_id || j.job_id,
            job_id: j.job_id,
            name: projectTitle(j),
            prompt: j.prompt,
            path: j.output_location || null,
            kind: "output",
            status: j.status,
            ready: !!j.ready,
            duration: j.output_duration ?? j.duration,
            created_at: j.completed_at || j.created_at,
            downloadable: !!j.ready,
          });
        });
    } catch (err) {
      grid.replaceChildren(emptyState({ title: "Could not load jobs", body: err.message || "Error" }));
      return;
    }

    items.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
    paint();
  }

  search.addEventListener("input", () => { query = search.value.trim(); paint(); });
  kindSel.addEventListener("change", () => { kind = kindSel.value; paint(); });
  refreshBtn.addEventListener("click", () => load());

  await load();

  return () => {
    Object.values(urls).forEach((u) => {
      try { URL.revokeObjectURL(u); } catch { /* ignore */ }
    });
  };
}

export default renderAssets;

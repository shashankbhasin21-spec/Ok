/**
 * API reference — sanitized endpoint list and auth guidance.
 */

import { el, clear } from "../ui.js";

const ENDPOINTS = [
  { method: "GET", path: "/health", auth: false, note: "Public. generation_available, ready_engines, queue_depth, worker, CUDA/VRAM details." },
  { method: "GET", path: "/version", auth: false, note: "Gateway version string." },
  { method: "GET", path: "/v1/providers", auth: true, note: "Engine status, capabilities, tasks (text-to-video, image-to-video)." },
  { method: "GET", path: "/v1/providers/health", auth: true, note: "Provider snapshot plus worker health summary." },
  { method: "POST", path: "/v1/videos", auth: true, note: "Create job: prompt, duration, aspect_ratio, engine, input_image, idempotency_key, …" },
  { method: "GET", path: "/v1/videos/{id}", auth: true, note: "Job lifecycle status and social-contract fields." },
  { method: "GET", path: "/v1/videos/{id}/events", auth: true, note: "Event log for a job." },
  { method: "POST", path: "/v1/videos/{id}/cancel", auth: true, note: "Cancel a non-terminal job." },
  { method: "GET", path: "/v1/videos/{id}/download", auth: true, note: "READY assets only (QC passed)." },
  { method: "GET", path: "/v1/videos/{id}/thumbnail", auth: true, note: "Thumbnail when available." },
  { method: "GET", path: "/v1/videos/{id}/preview", auth: true, note: "Preview media when available." },
  { method: "GET", path: "/v1/jobs", auth: true, note: "List jobs (?limit=&status=)." },
  { method: "GET", path: "/v1/metrics", auth: true, note: "Stored operational metrics only." },
  { method: "POST", path: "/v1/assets/upload", auth: true, note: "Multipart file upload for reference stills." },
  { method: "GET", path: "/v1/assets", auth: true, note: "List uploaded assets." },
];

export async function renderApiDocs(root, ctx) {
  clear(root);

  const page = el("div", { className: "ls-page" });
  page.append(el("header", { className: "ls-hero ls-hero--compact" }, [
    el("div", { className: "ls-hero__brand", text: "Lumen" }),
    el("h2", { className: "ls-hero__title", text: "API" }),
    el("p", { className: "ls-hero__lede", text: "Gateway reference for operators — no secrets embedded." }),
  ]));

  const table = el("table", { className: "ls-table" });
  table.append(el("thead", {}, [
    el("tr", {}, [
      el("th", { text: "Method" }),
      el("th", { text: "Path" }),
      el("th", { text: "Auth" }),
      el("th", { text: "Notes" }),
    ]),
  ]));
  const tbody = el("tbody");
  ENDPOINTS.forEach((ep) => {
    tbody.append(el("tr", {}, [
      el("td", {}, [el("code", { text: ep.method })]),
      el("td", {}, [el("code", { text: ep.path })]),
      el("td", { text: ep.auth ? "Required" : "Public" }),
      el("td", { text: ep.note }),
    ]));
  });
  table.append(tbody);

  page.append(
    el("section", { className: "ls-panel" }, [
      el("h3", { text: "Authentication" }),
      el("p", { text: "Send header " }),
      el("code", { text: "X-API-Key: <your key>" }),
      el("p", {
        className: "ls-field__hint",
        text: "Configure the key in Settings. Public routes: /health and /version. All /v1/* routes require auth when the gateway enforces API_KEY.",
      }),
      el("pre", {
        className: "ls-code-block",
        text:
          `curl -s "$GW/health"\n` +
          `curl -s -H "X-API-Key: $API_KEY" "$GW/v1/providers"\n` +
          `curl -s -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \\\n` +
          `  -d '{"prompt":"…","duration":30,"aspect_ratio":"16:9"}' \\\n` +
          `  "$GW/v1/videos"`,
      }),
    ]),
    el("section", { className: "ls-panel" }, [
      el("h3", { text: "Endpoints" }),
      el("div", { className: "ls-table-wrap" }, [table]),
    ]),
    el("section", { className: "ls-panel" }, [
      el("h3", { text: "Job lifecycle" }),
      el("p", { text: "QUEUED → PLANNING → ROUTING → DOWNLOADING_MODEL → RENDERING → STITCHING → AUDIO → QC → COMPLETED" }),
      el("p", { className: "ls-field__hint", text: "Terminal: COMPLETED, FAILED, CANCELLED. Download only when ready=true." }),
    ]),
    el("section", { className: "ls-panel" }, [
      el("h3", { text: "Engines" }),
      el("ul", { className: "ls-list" }, [
        el("li", { text: "wan — text-to-video + image-to-video" }),
        el("li", { text: "ltx — text-to-video + image-to-video + extend" }),
        el("li", { text: "framepack — image-to-video only" }),
      ]),
      el("p", {
        className: "ls-field__hint",
        text: "Never treat the gateway process as READY without provider.ready / generation_available evidence.",
      }),
    ])
  );

  root.append(page);
  return () => {};
}

export default renderApiDocs;

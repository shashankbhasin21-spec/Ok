import { el } from "../ui.js";

const ENDPOINTS = [
  ["GET", "/health", "Public health + generation_available"],
  ["GET", "/version", "Gateway version"],
  ["GET", "/v1/providers", "Engine capability snapshot"],
  ["GET", "/v1/providers/health", "Providers + worker summary"],
  ["POST", "/v1/videos", "Create generation job"],
  ["GET", "/v1/videos/{id}", "Job status"],
  ["GET", "/v1/videos/{id}/events", "Event log"],
  ["POST", "/v1/videos/{id}/cancel", "Cancel job"],
  ["GET", "/v1/videos/{id}/download", "Download READY MP4"],
  ["GET", "/v1/videos/{id}/preview", "Inline READY preview"],
  ["GET", "/v1/videos/{id}/thumbnail", "Thumbnail if present"],
  ["GET", "/v1/jobs", "List jobs"],
  ["GET", "/v1/metrics", "Stored metrics"],
  ["POST", "/v1/assets/upload", "Upload reference image"],
  ["GET", "/v1/assets", "List uploads + READY videos"],
];

export default async function renderApiDocs(root) {
  root.append(el("div", { className: "ls-page", style: "max-width:800px" }, [
    el("header", { className: "ls-hero" }, [
      el("h2", { className: "ls-hero__title", text: "API" }),
      el("p", { className: "ls-hero__lede", text: "Authenticate with header X-API-Key. Never paste secrets into prompts or commits." }),
    ]),
    el("div", { className: "ls-panel", style: "padding:0;overflow:hidden" }, [
      el("div", { className: "ls-queue-list", style: "padding:0.5rem" }, ENDPOINTS.map(([method, path, note]) =>
        el("div", { className: "ls-queue-row", style: "grid-template-columns:88px 1fr" }, [
          el("span", { className: "ls-badge ls-badge--accent", text: method }),
          el("div", {}, [
            el("div", { className: "ls-queue-row__title", style: "font-family:ui-monospace,monospace", text: path }),
            el("div", { className: "ls-queue-row__meta", text: note }),
          ]),
        ])
      )),
    ]),
  ]));
  return () => {};
}

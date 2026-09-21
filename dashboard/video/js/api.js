/** Authenticated API client — never logs secrets. */

const KEY_STORAGE = "vera_api_key";

export function getApiKey() {
  return localStorage.getItem(KEY_STORAGE) || "";
}

export function setApiKey(key) {
  if (key) localStorage.setItem(KEY_STORAGE, key);
  else localStorage.removeItem(KEY_STORAGE);
}

function headers(extra = {}) {
  const h = { ...extra };
  const key = getApiKey();
  if (key) h["X-API-Key"] = key;
  return h;
}

export class ApiError extends Error {
  constructor(message, { status, detail } = {}) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function parse(res) {
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    const detail = data.detail || data.error || res.statusText;
    const msg = typeof detail === "string" ? detail : JSON.stringify(detail);
    throw new ApiError(msg, { status: res.status, detail });
  }
  return data;
}

export async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: headers({
      ...(opts.body && !(opts.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...(opts.headers || {}),
    }),
  });
  return parse(res);
}

export async function health() {
  return api("/health");
}

export async function readiness() {
  return api("/v1/readiness");
}

export async function providers() {
  return api("/v1/providers");
}

export async function metrics() {
  return api("/v1/metrics");
}

export async function listJobs({ limit = 50, status } = {}) {
  const q = new URLSearchParams({ limit: String(limit) });
  if (status) q.set("status", status);
  return api(`/v1/jobs?${q}`);
}

export async function getJob(jobId) {
  return api(`/v1/videos/${jobId}`);
}

export async function getEvents(jobId) {
  return api(`/v1/videos/${jobId}/events`);
}

export async function createVideo(body) {
  return api("/v1/videos", { method: "POST", body: JSON.stringify(body) });
}

export async function cancelJob(jobId) {
  return api(`/v1/videos/${jobId}/cancel`, { method: "POST" });
}

export async function uploadImage(file) {
  const fd = new FormData();
  fd.append("file", file);
  return api("/v1/uploads/image", { method: "POST", body: fd });
}

export function downloadUrl(jobId) {
  return `/v1/videos/${jobId}/download`;
}

export function thumbnailUrl(jobId) {
  return `/v1/videos/${jobId}/thumbnail`;
}

export async function fetchBlob(path) {
  const res = await fetch(path, { headers: headers() });
  if (!res.ok) throw new ApiError(res.statusText, { status: res.status });
  return res.blob();
}

export async function downloadJob(jobId, filename) {
  const blob = await fetchBlob(downloadUrl(jobId));
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || `${jobId}.mp4`;
  a.click();
  URL.revokeObjectURL(url);
}

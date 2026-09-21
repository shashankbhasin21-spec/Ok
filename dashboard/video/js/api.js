const KEY = "lumen_api_key";

function getApiKey() {
  return localStorage.getItem(KEY) || "";
}

function setApiKey(value) {
  if (value) localStorage.setItem(KEY, value);
  else localStorage.removeItem(KEY);
}

async function parseBody(res) {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

function errorMessage(data, res) {
  if (!data) return res.statusText || `HTTP ${res.status}`;
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) {
    return data.detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  }
  if (data.error) return String(data.error);
  if (data.raw) return String(data.raw).slice(0, 240);
  return res.statusText || `HTTP ${res.status}`;
}

export async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  const key = getApiKey();
  if (key) headers["X-API-Key"] = key;
  if (opts.body && !(opts.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, { ...opts, headers });
  const data = await parseBody(res);
  if (!res.ok) {
    const err = new Error(errorMessage(data, res));
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

export async function apiBlob(path) {
  const headers = {};
  const key = getApiKey();
  if (key) headers["X-API-Key"] = key;
  const res = await fetch(path, { headers });
  if (!res.ok) {
    const data = await parseBody(res);
    const err = new Error(errorMessage(data, res));
    err.status = res.status;
    throw err;
  }
  return res.blob();
}

export const client = {
  getApiKey,
  setApiKey,
  health: () => api("/health"),
  version: () => api("/version"),
  providers: () => api("/v1/providers"),
  providersHealth: () => api("/v1/providers/health"),
  jobs: (params = {}) => {
    const q = new URLSearchParams();
    if (params.limit) q.set("limit", String(params.limit));
    if (params.status) q.set("status", params.status);
    const qs = q.toString();
    return api(`/v1/jobs${qs ? `?${qs}` : ""}`);
  },
  metrics: () => api("/v1/metrics"),
  createVideo: (body) => api("/v1/videos", { method: "POST", body: JSON.stringify(body) }),
  getVideo: (id) => api(`/v1/videos/${id}`),
  events: (id) => api(`/v1/videos/${id}/events`),
  cancel: (id) => api(`/v1/videos/${id}/cancel`, { method: "POST" }),
  downloadBlob: (id) => apiBlob(`/v1/videos/${id}/download`),
  previewBlob: (id) => apiBlob(`/v1/videos/${id}/preview`),
  thumbnailBlob: (id) => apiBlob(`/v1/videos/${id}/thumbnail`),
  uploadAsset: (file) => {
    const fd = new FormData();
    fd.append("file", file);
    return api("/v1/assets/upload", { method: "POST", body: fd });
  },
  listAssets: (limit = 100) => api(`/v1/assets?limit=${limit}`),
  assetFileBlob: (assetId) => apiBlob(`/v1/assets/${assetId}/file`),
};

export default client;

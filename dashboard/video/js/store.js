/** Persistent client state — jobs, settings, drafts. */

const ACTIVE_KEY = "vera_active_jobs";
const SETTINGS_KEY = "vera_settings";
const DRAFT_KEY = "vera_draft";

const listeners = new Set();

export const state = {
  route: "home",
  readiness: null,
  providers: [],
  jobs: [],
  metrics: null,
  health: null,
  selectedJobId: null,
  submitting: false,
  sidebarCollapsed: localStorage.getItem("vera_sidebar") === "1",
  galleryView: localStorage.getItem("vera_gallery") || "grid",
};

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function emit() {
  listeners.forEach((fn) => {
    try {
      fn(state);
    } catch (e) {
      console.error(e);
    }
  });
}

export function patch(partial) {
  Object.assign(state, partial);
  emit();
}

export function getActiveJobIds() {
  try {
    return JSON.parse(localStorage.getItem(ACTIVE_KEY) || "[]");
  } catch {
    return [];
  }
}

export function rememberActiveJob(jobId) {
  const ids = new Set(getActiveJobIds());
  ids.add(jobId);
  localStorage.setItem(ACTIVE_KEY, JSON.stringify([...ids]));
}

export function forgetActiveJob(jobId) {
  const ids = getActiveJobIds().filter((id) => id !== jobId);
  localStorage.setItem(ACTIVE_KEY, JSON.stringify(ids));
}

export function clearTerminalJobs(jobs) {
  const terminal = new Set(
    jobs
      .filter((j) => ["COMPLETED", "FAILED", "CANCELLED"].includes(j.status))
      .map((j) => j.job_id)
  );
  const kept = getActiveJobIds().filter((id) => !terminal.has(id));
  localStorage.setItem(ACTIVE_KEY, JSON.stringify(kept));
}

export function loadSettings() {
  try {
    return {
      allowShort: true,
      defaultAspect: "9:16",
      defaultDuration: 5,
      defaultEngine: "auto",
      defaultQuality: "draft",
      ...(JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") || {}),
    };
  } catch {
    return {
      allowShort: true,
      defaultAspect: "9:16",
      defaultDuration: 5,
      defaultEngine: "auto",
      defaultQuality: "draft",
    };
  }
}

export function saveSettings(s) {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
}

export function loadDraft() {
  try {
    return JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
  } catch {
    return null;
  }
}

export function saveDraft(draft) {
  localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
}

export function setSidebarCollapsed(v) {
  state.sidebarCollapsed = !!v;
  localStorage.setItem("vera_sidebar", v ? "1" : "0");
  emit();
}

export function setGalleryView(v) {
  state.galleryView = v;
  localStorage.setItem("vera_gallery", v);
  emit();
}

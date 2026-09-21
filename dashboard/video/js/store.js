const ACTIVE_KEY = "lumen_active_jobs";
const PREFS_KEY = "lumen_prefs";

const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
const ACTIVE = new Set([
  "QUEUED",
  "PLANNING",
  "ROUTING",
  "DOWNLOADING_MODEL",
  "RENDERING",
  "STITCHING",
  "AUDIO",
  "QC",
]);

const listeners = new Set();

function loadJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

export const store = {
  health: null,
  providers: [],
  jobs: [],
  metrics: null,
  assets: [],
  route: "home",
  booting: true,
  lastError: null,
  prefs: loadJson(PREFS_KEY, {
    defaultAspect: "9:16",
    defaultDuration: 30,
    defaultQuality: "high",
    defaultEngine: "auto",
  }),

  get activeJobIds() {
    return loadJson(ACTIVE_KEY, []);
  },

  setActiveJobIds(ids) {
    const unique = [...new Set(ids.filter(Boolean))];
    localStorage.setItem(ACTIVE_KEY, JSON.stringify(unique));
    this.emit("activeJobs");
  },

  trackJob(id) {
    if (!id) return;
    const ids = this.activeJobIds;
    if (!ids.includes(id)) {
      ids.unshift(id);
      this.setActiveJobIds(ids.slice(0, 40));
    }
  },

  untrackJob(id) {
    this.setActiveJobIds(this.activeJobIds.filter((x) => x !== id));
  },

  pruneTerminal(jobsById) {
    const next = this.activeJobIds.filter((id) => {
      const j = jobsById[id];
      if (!j) return true;
      return !TERMINAL.has(j.status);
    });
    this.setActiveJobIds(next);
  },

  savePrefs(partial) {
    this.prefs = { ...this.prefs, ...partial };
    localStorage.setItem(PREFS_KEY, JSON.stringify(this.prefs));
    this.emit("prefs");
  },

  subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },

  emit(type, payload) {
    listeners.forEach((fn) => {
      try {
        fn(type, payload);
      } catch (e) {
        console.warn("store listener error", e);
      }
    });
  },

  isTerminal: (status) => TERMINAL.has(status),
  isActive: (status) => ACTIVE.has(status),
  ACTIVE_STATUSES: ACTIVE,
  TERMINAL_STATUSES: TERMINAL,
};

export default store;

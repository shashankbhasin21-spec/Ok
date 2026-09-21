import { store } from "../store.js";
import { client } from "../api.js";

/**
 * Intelligent job polling with backoff, terminal stop, and refresh recovery.
 */
export function createPoller() {
  const timers = new Map();
  const listeners = new Map();

  function backoff(attempt) {
    return Math.min(8000, 900 + attempt * 400);
  }

  function stop(jobId) {
    const t = timers.get(jobId);
    if (t) clearTimeout(t);
    timers.delete(jobId);
  }

  function stopAll() {
    for (const id of timers.keys()) stop(id);
  }

  function subscribe(jobId, fn) {
    if (!listeners.has(jobId)) listeners.set(jobId, new Set());
    listeners.get(jobId).add(fn);
    return () => listeners.get(jobId)?.delete(fn);
  }

  function emit(jobId, job) {
    listeners.get(jobId)?.forEach((fn) => {
      try { fn(job); } catch (e) { console.warn(e); }
    });
    store.emit("job", job);
  }

  async function tick(jobId, attempt = 0) {
    try {
      const job = await client.getVideo(jobId);
      emit(jobId, job);
      if (store.isTerminal(job.status)) {
        store.untrackJob(jobId);
        stop(jobId);
        return;
      }
      store.trackJob(jobId);
      stop(jobId);
      timers.set(jobId, setTimeout(() => tick(jobId, 0), backoff(0)));
    } catch (err) {
      store.emit("poll-error", { jobId, err });
      const next = attempt + 1;
      stop(jobId);
      timers.set(jobId, setTimeout(() => tick(jobId, next), backoff(next)));
    }
  }

  function watch(jobId) {
    if (!jobId) return;
    store.trackJob(jobId);
    if (timers.has(jobId)) return;
    tick(jobId, 0);
  }

  function resumeActive() {
    for (const id of store.activeJobIds) watch(id);
  }

  return { watch, stop, stopAll, subscribe, resumeActive };
}

export default createPoller;

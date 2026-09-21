/** Vera Studio — application shell, routing, polling, command palette. */

import * as api from "./api.js";
import { humanFailure, STAGES, stageIndex } from "./capabilities.js";
import { toast as showToast } from "./motion.js";
import {
  clearTerminalJobs,
  getActiveJobIds,
  patch,
  rememberActiveJob,
  setSidebarCollapsed,
  state,
  subscribe,
} from "./store.js";
import * as views from "./views.js";

const TITLES = {
  home: "Home",
  create: "Create",
  i2v: "Image → Video",
  t2v: "Text → Video",
  projects: "Projects",
  assets: "Assets",
  queue: "Generation Queue",
  models: "Models",
  analytics: "Analytics",
  system: "System",
  settings: "Settings",
  api: "API",
  account: "Account",
};

const COMMANDS = [
  { id: "create", label: "New generation", route: "create" },
  { id: "i2v", label: "Image → Video", route: "i2v" },
  { id: "t2v", label: "Text → Video", route: "t2v" },
  { id: "projects", label: "Open Projects", route: "projects" },
  { id: "queue", label: "Open Queue", route: "queue" },
  { id: "system", label: "System Status", route: "system" },
  { id: "settings", label: "Settings", route: "settings" },
  { id: "models", label: "Models", route: "models" },
  { id: "analytics", label: "Analytics", route: "analytics" },
];

let pollTimer = null;
let pollBackoff = 1500;
let currentJobPoll = null;

const ctx = {
  toast,
  navigate,
  submitGeneration,
  refreshJobs,
  bootstrap,
  signOut,
};

function $(sel) {
  return document.querySelector(sel);
}

function toast(opts) {
  showToast($("#toastHost"), opts);
}

function setReadyPill() {
  const r = state.readiness || {};
  const dot = $("#readyDot");
  const text = $("#readyText");
  if (!dot || !text) return;
  if (r.generation_available) {
    dot.className = "dot ok";
    text.textContent = `Ready · ${(r.ready_engines || []).join(", ") || "engine"}`;
  } else if (r.worker_available || state.health?.details?.worker_configured) {
    dot.className = "dot warn";
    text.textContent = "Worker up · engines not ready";
  } else {
    dot.className = "dot bad";
    text.textContent = "Generation unavailable";
  }
  const badge = $("#queueBadge");
  const active = (state.jobs || []).filter((j) => views.ACTIVE.has(j.status)).length;
  if (badge) {
    badge.hidden = active === 0;
    badge.textContent = String(active);
  }
}

async function bootstrap() {
  try {
    const [health, readiness, providers, jobs, metrics] = await Promise.all([
      api.health(),
      api.readiness().catch(() => null),
      api.providers(),
      api.listJobs({ limit: 100 }),
      api.metrics().catch(() => null),
    ]);
    patch({
      health,
      readiness: readiness || {
        generation_available: health.generation_available,
        ready_engines: health.ready_engines,
        cuda_available: health.details?.cuda_available,
        cpu_assembly_ready: health.details?.cpu_assembly_ready,
        worker_available: health.details?.worker_available,
        ltx_ready: (health.ready_engines || []).includes("ltx"),
        vram_total_mb: health.details?.vram_total_mb,
        vram_free_mb: health.details?.vram_free_mb,
        queue_depth: health.queue_depth,
      },
      providers,
      jobs: jobs.jobs || [],
      metrics,
    });
    clearTerminalJobs(jobs.jobs || []);
    setReadyPill();
    schedulePoll();
    resumeActiveJobs();
  } catch (err) {
    if (err.status === 401 || err.status === 503) {
      signOut(err.message);
      return;
    }
    toast({ title: "Could not load studio", body: err.message, tone: "error" });
  }
}

function resumeActiveJobs() {
  const ids = getActiveJobIds();
  ids.forEach((id) => trackJob(id));
}

async function refreshJobs() {
  try {
    const jobs = await api.listJobs({ limit: 100 });
    patch({ jobs: jobs.jobs || [] });
    setReadyPill();
    if (["projects", "assets", "queue", "home"].includes(state.route)) {
      renderRoute(state.route, { soft: true });
    }
  } catch (err) {
    console.warn("refreshJobs", err.message);
  }
}

function schedulePoll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    const hasActive = (state.jobs || []).some((j) => views.ACTIVE.has(j.status));
    if (hasActive || document.visibilityState === "visible") {
      await refreshJobs();
      try {
        const readiness = await api.readiness();
        patch({ readiness });
        setReadyPill();
        pollBackoff = 1500;
      } catch {
        pollBackoff = Math.min(pollBackoff * 1.5, 12000);
      }
    }
    schedulePoll();
  }, pollBackoff);
}

function navigate(route) {
  const next = TITLES[route] ? route : "home";
  const root = $("#viewRoot");
  if (!root) return;
  root.classList.add("leaving");
  const go = () => {
    root.classList.remove("leaving");
    patch({ route: next });
    renderRoute(next);
    history.replaceState({ route: next }, "", `#${next}`);
    document.querySelectorAll("[data-nav]").forEach((el) => {
      el.classList.toggle("active", el.dataset.nav === next);
    });
    document.querySelectorAll(".tab-item").forEach((el) => {
      el.classList.toggle("active", el.dataset.nav === next);
    });
    $("#pageTitle").textContent = TITLES[next] || "Vera";
    $("#sidebar")?.classList.remove("open");
  };
  setTimeout(go, 120);
}

function renderRoute(route, { soft = false } = {}) {
  const root = $("#viewRoot");
  if (!root) return;
  if (!soft) views.revokeUrls();
  switch (route) {
    case "home":
      views.renderHome(root, ctx);
      break;
    case "create":
      views.renderStudio(root, ctx, { mode: "create" });
      break;
    case "i2v":
      views.renderStudio(root, ctx, { mode: "i2v" });
      break;
    case "t2v":
      views.renderStudio(root, ctx, { mode: "t2v" });
      break;
    case "projects":
      views.renderProjects(root, ctx);
      break;
    case "assets":
      views.renderAssets(root, ctx);
      break;
    case "queue":
      views.renderQueue(root, ctx);
      break;
    case "models":
      views.renderModels(root);
      break;
    case "analytics":
      views.renderAnalytics(root);
      break;
    case "system":
      views.renderSystem(root);
      break;
    case "settings":
      views.renderSettings(root, ctx);
      break;
    case "api":
      views.renderApi(root);
      break;
    case "account":
      views.renderAccount(root, ctx);
      break;
    default:
      views.renderHome(root, ctx);
  }
}

async function submitGeneration(body, { studio = false } = {}) {
  if (state.submitting) return;
  patch({ submitting: true });
  try {
    const created = await api.createVideo(body);
    rememberActiveJob(created.job_id);
    toast({ title: "Generation queued", body: created.job_id.slice(0, 12), tone: "success" });
    if (studio || state.route === "create" || state.route === "i2v" || state.route === "t2v") {
      // stay on studio
    } else {
      navigate("create");
    }
    await refreshJobs();
    trackJob(created.job_id);
  } catch (err) {
    toast({ title: "Could not start generation", body: err.message, tone: "error" });
  } finally {
    patch({ submitting: false });
  }
}

function trackJob(jobId) {
  if (currentJobPoll) clearInterval(currentJobPoll);
  const tick = async () => {
    try {
      const job = await api.getJob(jobId);
      patch({ selectedJobId: jobId });
      views.updateLiveProgress(job, ctx);
      if (["COMPLETED", "FAILED", "CANCELLED"].includes(job.status)) {
        clearInterval(currentJobPoll);
        currentJobPoll = null;
        views.forgetActiveJob(jobId);
        await refreshJobs();
        if (job.status === "COMPLETED") {
          toast({ title: "Ready to watch", body: "Your MP4 passed QC.", tone: "success" });
        } else if (job.status === "FAILED") {
          toast({ title: "Generation failed", body: job.failure_category || "", tone: "error" });
        }
      }
    } catch (err) {
      console.warn("poll", err.message);
    }
  };
  tick();
  currentJobPoll = setInterval(tick, 1400);
}

async function openJobModal(jobId) {
  const host = $("#modalHost");
  host.innerHTML = `<div class="modal-backdrop" role="presentation"><div class="modal" role="dialog" aria-modal="true"><div class="skeleton" style="height:240px"></div></div></div>`;
  const close = () => {
    host.innerHTML = "";
  };
  host.querySelector(".modal-backdrop")?.addEventListener("click", (e) => {
    if (e.target.classList.contains("modal-backdrop")) close();
  });
  try {
    const job = await api.getJob(jobId);
    const modal = host.querySelector(".modal");
    modal.innerHTML = `
      <div class="modal-head">
        <div>
          <h2 class="display" style="font-size:1.8rem;margin:0">${escape(job.prompt || "Generation").slice(0, 80)}</h2>
          <div class="gen-meta">${job.status} · ${job.engine || "—"} · ${job.job_id}</div>
        </div>
        <button type="button" class="icon-btn" data-close aria-label="Close">✕</button>
      </div>
      <div id="modalPlayer"></div>
      ${
        job.status === "FAILED"
          ? `<div class="details-box"><strong>${escape(
              humanFailure(job).summary
            )}</strong><div>${escape(job.failure_reason || "")}</div></div>`
          : ""
      }
      ${stageHtml(job.status)}
    `;
    // humanFailure is not exported from views as views.humanFailure - fix by importing
    modal.querySelector("[data-close]")?.addEventListener("click", close);
    if (job.ready) {
      const mount = modal.querySelector("#modalPlayer");
      const blob = await api.fetchBlob(api.downloadUrl(job.job_id));
      const url = URL.createObjectURL(blob);
      const { mountPlayer } = await import("./player.js");
      mountPlayer(mount, {
        jobId: job.job_id,
        srcObjectUrl: url,
        onRegenerate: () => {
          close();
          submitGeneration({
            prompt: job.prompt,
            engine: job.engine_requested || "auto",
            aspect_ratio: job.aspect_ratio,
            duration: job.duration,
            allow_short: true,
            idempotency_key: `vera-regen-${Date.now()}`,
          });
        },
        onDuplicate: () => {
          close();
          navigate("create");
        },
      });
    } else {
      views.updateLiveProgress(job, ctx);
      trackJob(job.job_id);
    }
  } catch (err) {
    host.innerHTML = "";
    toast({ title: "Could not open job", body: err.message, tone: "error" });
  }
}

function escape(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function stageHtml(status) {
  if (status === "FAILED" || status === "CANCELLED") {
    return `<div class="stage-track"><span class="stage-pill failed">${status}</span></div>`;
  }
  const idx = stageIndex(status);
  return `<div class="stage-track" style="margin-top:0.75rem">${STAGES.map((s, i) => {
    let cls = "stage-pill";
    if (status === "COMPLETED" || i < idx) cls += " done";
    else if (i === idx) cls += " current";
    return `<span class="${cls}">${s.replaceAll("_", " ")}</span>`;
  }).join("")}</div>`;
}

function openCommandPalette() {
  const overlay = $("#cmdPalette");
  const input = $("#cmdInput");
  const list = $("#cmdList");
  overlay.hidden = false;
  let idx = 0;
  const render = (q = "") => {
    const items = COMMANDS.filter((c) => c.label.toLowerCase().includes(q.toLowerCase()));
    list.innerHTML = items
      .map(
        (c, i) =>
          `<li><button type="button" role="option" aria-selected="${
            i === idx ? "true" : "false"
          }" data-route="${c.route}">${c.label}</button></li>`
      )
      .join("");
  };
  render();
  input.value = "";
  input.focus();
  const onKey = (e) => {
    const buttons = [...list.querySelectorAll("button")];
    if (e.key === "Escape") {
      close();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      idx = Math.min(idx + 1, buttons.length - 1);
      render(input.value);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      idx = Math.max(idx - 1, 0);
      render(input.value);
    } else if (e.key === "Enter") {
      e.preventDefault();
      buttons[idx]?.click();
    }
  };
  const onInput = () => {
    idx = 0;
    render(input.value);
  };
  const onClick = (e) => {
    const route = e.target.closest("[data-route]")?.dataset.route;
    if (route) {
      close();
      navigate(route);
    }
  };
  function close() {
    overlay.hidden = true;
    input.removeEventListener("keydown", onKey);
    input.removeEventListener("input", onInput);
    list.removeEventListener("click", onClick);
  }
  input.addEventListener("keydown", onKey);
  input.addEventListener("input", onInput);
  list.addEventListener("click", onClick);
  overlay.addEventListener(
    "click",
    (e) => {
      if (e.target === overlay) close();
    },
    { once: true }
  );
}

function signOut(message) {
  $("#appShell").hidden = true;
  $("#authGate").hidden = false;
  if (message) {
    const err = $("#gateError");
    err.hidden = false;
    err.textContent = message;
  }
}

function enterStudio() {
  $("#authGate").hidden = true;
  $("#appShell").hidden = false;
  const shell = $("#appShell");
  shell.classList.toggle("collapsed", state.sidebarCollapsed);
  const route = (location.hash || "#home").replace("#", "") || "home";
  navigate(route);
  bootstrap();
}

function wireShell() {
  document.body.addEventListener("click", (e) => {
    const nav = e.target.closest("[data-nav]")?.dataset.nav;
    if (nav) {
      e.preventDefault();
      navigate(nav);
      return;
    }
    const jobId = e.target.closest("[data-open-job]")?.dataset.openJob;
    if (jobId) {
      openJobModal(jobId);
    }
  });

  $("#sidebarToggle")?.addEventListener("click", () => {
    setSidebarCollapsed(!state.sidebarCollapsed);
    $("#appShell").classList.toggle("collapsed", state.sidebarCollapsed);
  });
  $("#mobileMenuBtn")?.addEventListener("click", () => {
    $("#sidebar")?.classList.toggle("open");
  });
  $("#cmdOpen")?.addEventListener("click", openCommandPalette);
  window.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      openCommandPalette();
    }
  });

  $("#gateEnter")?.addEventListener("click", async () => {
    const key = $("#gateApiKey").value.trim();
    if (!key) {
      $("#gateError").hidden = false;
      $("#gateError").textContent = "Enter your API key.";
      return;
    }
    api.setApiKey(key);
    try {
      await api.providers();
      $("#gateError").hidden = true;
      enterStudio();
    } catch (err) {
      api.setApiKey("");
      $("#gateError").hidden = false;
      $("#gateError").textContent = err.message || "Authentication failed";
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refreshJobs();
  });
}

function init() {
  wireShell();
  subscribe(() => setReadyPill());
  if (api.getApiKey()) {
    $("#gateApiKey").value = api.getApiKey();
    enterStudio();
  } else {
    $("#authGate").hidden = false;
    $("#appShell").hidden = true;
  }
}

init();

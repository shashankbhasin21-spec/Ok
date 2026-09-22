import { client } from "./api.js";
import { store } from "./store.js";
import { parseRoute, navigate, routeTitle } from "./router.js";
import { magneticButton, animateIn } from "./motion.js";
import { clear } from "./ui.js";
import { toast } from "./components/toast.js";
import { createCommandPalette } from "./components/commandPalette.js";
import { createPoller } from "./poller.js";

const PAGE_LOADERS = {
  home: () => import("./pages/home.js"),
  create: () => import("./pages/create.js"),
  "image-video": () => import("./pages/imageVideo.js"),
  "text-video": () => import("./pages/textVideo.js"),
  projects: () => import("./pages/projects.js"),
  assets: () => import("./pages/assets.js"),
  queue: () => import("./pages/queue.js"),
  models: () => import("./pages/models.js"),
  analytics: () => import("./pages/analytics.js"),
  system: () => import("./pages/system.js"),
  settings: () => import("./pages/settings.js"),
  api: () => import("./pages/apiDocs.js"),
  account: () => import("./pages/account.js"),
};

const appEl = document.getElementById("ls-app");
const viewEl = document.getElementById("ls-view");
const titleEl = document.getElementById("ls-page-title");
const subEl = document.getElementById("ls-page-sub");
const statusEl = document.getElementById("ls-sidebar-status");
const queueBadge = document.getElementById("ls-queue-badge");

let cleanup = null;
let refreshTimer = null;
const poller = createPoller();

const ctx = {
  api: client,
  store,
  poller,
  toast,
  navigate,
  motion: { animateIn, magneticButton },
};

function setSidebarExpanded(expanded) {
  appEl.dataset.sidebar = expanded ? "expanded" : "collapsed";
  const btn = document.getElementById("ls-sidebar-toggle");
  if (btn) btn.setAttribute("aria-expanded", expanded ? "true" : "false");
  localStorage.setItem("lumen_sidebar", expanded ? "1" : "0");
}

function setNavCurrent(route) {
  document.querySelectorAll("[data-nav]").forEach((btn) => {
    const is = btn.getAttribute("data-nav") === route;
    if (btn.classList.contains("ls-nav__item") || btn.classList.contains("ls-mobile-nav__item")) {
      btn.setAttribute("aria-current", is ? "page" : "false");
    }
  });
}

function updateStatusUi() {
  const h = store.health;
  const dot = statusEl?.querySelector(".ls-status-dot");
  const text = statusEl?.querySelector(".ls-sidebar__status-text");
  if (!dot || !text) return;
  if (!h) {
    dot.dataset.state = "unknown";
    text.textContent = "Checking…";
    return;
  }
  if (h.generation_available) {
    dot.dataset.state = "online";
    text.textContent = `Ready · ${h.ready_engines?.length || 0} engine${(h.ready_engines?.length || 0) === 1 ? "" : "s"}`;
  } else if (h.gateway === "ok") {
    dot.dataset.state = "degraded";
    text.textContent = h.gpu_worker_available ? "Worker connected · no ready engine" : "Gateway online · worker offline";
  } else {
    dot.dataset.state = "offline";
    text.textContent = "Gateway offline";
  }

  const active = (store.jobs || []).filter((j) => store.isActive(j.status)).length;
  if (queueBadge) {
    if (active > 0) {
      queueBadge.hidden = false;
      queueBadge.textContent = String(active);
    } else {
      queueBadge.hidden = true;
    }
  }
}

async function refreshCore() {
  try {
    const health = await client.health();
    store.health = health;
    store.emit("health", health);
  } catch (err) {
    store.health = null;
    store.lastError = err;
    store.emit("health", null);
  }

  if (client.getApiKey()) {
    try {
      const [providers, jobs, metrics] = await Promise.all([
        client.providers(),
        client.jobs({ limit: 50 }),
        client.metrics().catch(() => null),
      ]);
      store.providers = providers || [];
      store.jobs = jobs?.jobs || [];
      store.metrics = metrics;
      store.emit("data");
    } catch (err) {
      store.lastError = err;
      store.emit("auth-error", err);
    }
  }
  updateStatusUi();
}

async function renderRoute() {
  const { route } = parseRoute();
  store.route = route;
  const [title, sub] = routeTitle(route);
  titleEl.textContent = title;
  subEl.textContent = sub;
  setNavCurrent(route);
  appEl.dataset.mobileNav = "closed";

  if (cleanup) {
    try { cleanup(); } catch { /* ignore */ }
    cleanup = null;
  }

  viewEl.classList.add("is-leaving");
  await new Promise((r) => setTimeout(r, 120));
  clear(viewEl);
  viewEl.classList.remove("is-leaving");
  viewEl.append(elBootSkeleton());

  try {
    const mod = await PAGE_LOADERS[route]();
    clear(viewEl);
    const result = await mod.default(viewEl, ctx);
    cleanup = typeof result === "function" ? result : result?.destroy || null;
    animateIn(viewEl);
  } catch (err) {
    console.error(err);
    clear(viewEl);
    viewEl.innerHTML = `<div class="ls-empty"><h3>Couldn't load this view</h3><p>${escapeHtml(err.message || String(err))}</p></div>`;
  }
}

function elBootSkeleton() {
  const d = document.createElement("div");
  d.className = "ls-page";
  d.innerHTML = `
    <div class="ls-skeleton ls-skeleton--title"></div>
    <div class="ls-skeleton ls-skeleton--text"></div>
    <div class="ls-skeleton ls-skeleton--media" style="margin-top:1rem"></div>`;
  return d;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function wireShell() {
  document.querySelectorAll("[data-nav]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const route = btn.getAttribute("data-nav");
      if (route) navigate(route);
    });
  });

  document.getElementById("ls-sidebar-toggle")?.addEventListener("click", () => {
    setSidebarExpanded(appEl.dataset.sidebar !== "expanded");
  });

  document.getElementById("ls-mobile-menu")?.addEventListener("click", () => {
    appEl.dataset.mobileNav = appEl.dataset.mobileNav === "open" ? "closed" : "open";
  });

  document.addEventListener("click", (e) => {
    if (appEl.dataset.mobileNav === "open" && !e.target.closest(".ls-sidebar") && !e.target.closest("#ls-mobile-menu")) {
      appEl.dataset.mobileNav = "closed";
    }
  });

  const saved = localStorage.getItem("lumen_sidebar");
  if (saved === "0" && window.innerWidth > 1024) setSidebarExpanded(false);

  document.querySelectorAll(".ls-btn--magnetic").forEach((btn) => magneticButton(btn));
  createCommandPalette();
}

async function boot() {
  wireShell();
  if (!location.hash) location.hash = "#/home";
  await refreshCore();
  store.booting = false;
  poller.resumeActive();
  await renderRoute();
  window.addEventListener("hashchange", () => renderRoute());
  refreshTimer = setInterval(refreshCore, 12000);
  store.subscribe((type) => {
    if (type === "health" || type === "data" || type === "job") updateStatusUi();
  });
}

boot().catch((err) => {
  console.error(err);
  toast(err.message || "Failed to start studio", { type: "error", title: "Startup error" });
});

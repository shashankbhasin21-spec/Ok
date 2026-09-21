/**
 * Settings — API key, motion note, default generation prefs.
 */

import { el, clear, field, select, prefersReducedMotion } from "../ui.js";
import { toast } from "../components/toast.js";

export async function renderSettings(root, ctx) {
  const { api, store } = ctx;
  clear(root);

  const prefs = store.prefs || {};
  const page = el("div", { className: "ls-page" });
  page.append(el("header", { className: "ls-hero ls-hero--compact" }, [
    el("div", { className: "ls-hero__brand", text: "Lumen" }),
    el("h2", { className: "ls-hero__title", text: "Settings" }),
    el("p", { className: "ls-hero__lede", text: "Local preferences only — keys stay in your browser storage." }),
  ]));

  const keyInput = el("input", {
    type: "password",
    className: "ls-input",
    autocomplete: "off",
    spellcheck: "false",
    "aria-label": "API key",
    value: api.getApiKey() || "",
  });

  const aspect = select("defaultAspect", [
    { value: "9:16", label: "9:16" },
    { value: "16:9", label: "16:9" },
    { value: "1:1", label: "1:1" },
    { value: "4:5", label: "4:5" },
  ], prefs.defaultAspect || "9:16");

  const duration = el("input", {
    type: "number",
    className: "ls-input",
    min: "1",
    max: "600",
    value: String(prefs.defaultDuration || 30),
    "aria-label": "Default duration",
  });

  const quality = select("defaultQuality", [
    { value: "draft", label: "Draft" },
    { value: "standard", label: "Standard" },
    { value: "high", label: "High" },
    { value: "max", label: "Max" },
  ], prefs.defaultQuality || "high");

  const engine = select("defaultEngine", [
    { value: "auto", label: "Auto" },
    { value: "wan", label: "Wan" },
    { value: "ltx", label: "LTX" },
    { value: "framepack", label: "FramePack" },
  ], prefs.defaultEngine || "auto");

  const systemReduced = prefersReducedMotion();
  const reducedNote = el("p", {
    className: "ls-field__hint",
    text: systemReduced
      ? "Your system currently prefers reduced motion. Lumen Studio minimizes staged animations."
      : "System prefers full motion. Enable OS-level reduced motion anytime; the studio respects prefers-reduced-motion.",
  });

  const status = el("p", { className: "ls-composer__gate", role: "status" });
  const saveBtn = el("button", { type: "submit", className: "ls-btn ls-btn--primary", text: "Save" });
  const clearBtn = el("button", { type: "button", className: "ls-btn", text: "Clear API key" });

  const form = el("form", { className: "ls-panel ls-settings", "aria-label": "Settings" }, [
    el("h3", { text: "Authentication" }),
    field("API key (X-API-Key)", keyInput),
    el("p", { className: "ls-field__hint", text: "Stored in localStorage (lumen_api_key). Never committed or logged by this UI." }),

    el("h3", { text: "Defaults" }),
    el("div", { className: "ls-row" }, [
      field("Default aspect", aspect),
      field("Default duration (s)", duration),
      field("Default quality", quality),
      field("Default engine", engine),
    ]),

    el("h3", { text: "Motion" }),
    reducedNote,

    el("div", { className: "ls-toolbar" }, [saveBtn, clearBtn]),
    status,
  ]);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    api.setApiKey(keyInput.value.trim());
    store.savePrefs({
      defaultAspect: aspect.value,
      defaultDuration: Number(duration.value) || 30,
      defaultQuality: quality.value,
      defaultEngine: engine.value,
    });
    status.textContent = "Saved locally. Refreshing gateway data…";
    toast("Settings saved", { type: "success" });
    try {
      store.health = await api.health();
      if (api.getApiKey()) {
        const [providers, jobs, metrics] = await Promise.all([
          api.providers(),
          api.jobs({ limit: 50 }),
          api.metrics().catch(() => null),
        ]);
        store.providers = providers || [];
        store.jobs = jobs?.jobs || [];
        store.metrics = metrics;
      } else {
        store.providers = [];
        store.jobs = [];
        store.metrics = null;
      }
      store.emit("data");
      store.emit("health", store.health);
      status.textContent = "Saved. Gateway data refreshed.";
    } catch (err) {
      status.textContent = `Saved key, but refresh failed: ${err.message || err}`;
      toast(err.message || String(err), { type: "error", title: "Refresh" });
    }
  });

  clearBtn.addEventListener("click", () => {
    keyInput.value = "";
    api.setApiKey("");
    status.textContent = "API key cleared.";
    toast("API key cleared", { type: "info" });
  });

  page.append(form);
  root.append(page);

  return () => {};
}

export default renderSettings;

import { el, field } from "../ui.js";
import { toast } from "../components/toast.js";

export default async function renderSettings(root, ctx) {
  const { api, store } = ctx;
  const keyInput = el("input", {
    className: "ls-input",
    type: "password",
    value: api.getApiKey(),
    placeholder: "X-API-Key",
    autocomplete: "off",
    "aria-label": "API key",
  });
  const aspect = el("select", { className: "ls-select" }, [
    ...["9:16", "16:9", "1:1", "4:5"].map((v) => el("option", { value: v, text: v, selected: store.prefs.defaultAspect === v ? true : undefined })),
  ]);
  const duration = el("input", { className: "ls-input", type: "number", min: "1", max: "600", value: String(store.prefs.defaultDuration || 30) });
  const quality = el("select", { className: "ls-select" }, [
    ...["draft", "standard", "high", "max"].map((v) => el("option", { value: v, text: v, selected: store.prefs.defaultQuality === v ? true : undefined })),
  ]);

  const save = el("button", { type: "button", className: "ls-btn ls-btn--primary", text: "Save" });
  save.addEventListener("click", async () => {
    api.setApiKey(keyInput.value.trim());
    store.savePrefs({
      defaultAspect: aspect.value,
      defaultDuration: Number(duration.value || 30),
      defaultQuality: quality.value,
    });
    toast("Settings saved locally", { type: "success", title: "Settings" });
    try {
      store.health = await api.health();
      if (api.getApiKey()) {
        store.providers = await api.providers();
        store.jobs = (await api.jobs({ limit: 50 })).jobs || [];
        store.metrics = await api.metrics().catch(() => null);
      }
      store.emit("data");
    } catch (err) {
      toast(err.message, { type: "error", title: "Could not refresh" });
    }
  });

  root.append(el("div", { className: "ls-page", style: "max-width:560px" }, [
    el("div", { className: "ls-panel", style: "padding:1.25rem;display:flex;flex-direction:column;gap:1rem" }, [
      el("h2", { text: "Settings" }),
      el("p", { className: "ls-field__hint", text: "API key is stored in this browser only (localStorage). It is never committed or logged by the UI." }),
      field("API key", keyInput),
      field("Default aspect ratio", aspect),
      field("Default duration (s)", duration),
      field("Default quality", quality),
      el("p", { className: "ls-field__hint", text: "Motion respects prefers-reduced-motion automatically. No separate toggle required." }),
      save,
    ]),
  ]));

  return () => {};
}

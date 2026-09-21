/**
 * Text → Video — focused single-column T2V flow.
 */

import {
  el,
  clear,
  field,
  select,
  engineCapabilities,
  abridgeId,
  emptyState,
} from "../ui.js";
import { createGenerationCard } from "../components/generationCard.js";
import { toast } from "../components/toast.js";

function newIdempotencyKey() {
  return `t2v-${crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
}

export async function renderTextVideo(root, ctx) {
  const { api, store, poller, navigate } = ctx;
  clear(root);

  let lastBody = null;
  const prefs = store.prefs || {};

  const page = el("div", { className: "ls-page" });
  page.append(el("header", { className: "ls-hero ls-hero--compact" }, [
    el("div", { className: "ls-hero__brand", text: "Lumen" }),
    el("h2", { className: "ls-hero__title", text: "Text → Video" }),
    el("p", { className: "ls-hero__lede", text: "A focused path from prompt to open-model motion." }),
  ]));

  const unavailable = el("div", {});
  const panel = el("section", { className: "ls-panel" });

  const prompt = el("textarea", {
    className: "ls-textarea",
    rows: "6",
    placeholder: "Cinematic wide shot of…",
    "aria-label": "Prompt",
  });

  function engineOptions() {
    const opts = [{ value: "auto", label: "Auto" }];
    (store.providers || [])
      .filter((p) => (p.tasks || []).includes("text-to-video"))
      .forEach((p) => {
        opts.push({
          value: p.name,
          label: p.ready ? p.name : `${p.name} (not ready)`,
          disabled: !p.ready,
        });
      });
    return opts;
  }

  let engine = select("engine", engineOptions(), prefs.defaultEngine || "auto");
  const aspect = select("aspect_ratio", [
    { value: "9:16", label: "9:16" },
    { value: "16:9", label: "16:9" },
    { value: "1:1", label: "1:1" },
    { value: "4:5", label: "4:5" },
  ], prefs.defaultAspect || "9:16");
  const duration = el("input", {
    className: "ls-input", type: "number", min: "1", max: "600",
    value: String(prefs.defaultDuration || 30),
  });
  const resolution = select("resolution", [
    { value: "", label: "Auto" },
    { value: "720p", label: "720p" },
    { value: "1080p", label: "1080p" },
  ], "");
  const quality = select("quality", [
    { value: "draft", label: "Draft" },
    { value: "standard", label: "Standard" },
    { value: "high", label: "High" },
    { value: "max", label: "Max" },
  ], prefs.defaultQuality || "high");
  const neg = el("input", { className: "ls-input", placeholder: "Optional" });
  const seed = el("input", { className: "ls-input", type: "number", placeholder: "Random" });
  const audioCb = el("input", { type: "checkbox" });
  const capCb = el("input", { type: "checkbox" });
  const shortCb = el("input", { type: "checkbox" });
  const gate = el("div", { className: "ls-composer__gate", role: "status" });
  const genBtn = el("button", { type: "button", className: "ls-btn ls-btn--primary", text: "Generate", disabled: true });

  const form = el("div", {}, [
    field("Prompt", prompt),
    el("div", { className: "ls-row" }, [
      field("Model", engine),
      field("Aspect", aspect),
      field("Duration (s)", duration),
      field("Resolution", resolution),
      field("Quality", quality),
    ]),
    el("details", { className: "ls-details" }, [
      el("summary", { text: "Advanced" }),
      field("Negative prompt", neg),
      field("Seed", seed),
      el("label", { className: "ls-field ls-field--check" }, [audioCb, el("span", { text: "Audio" })]),
      el("label", { className: "ls-field ls-field--check" }, [capCb, el("span", { text: "Captions" })]),
      el("label", { className: "ls-field ls-field--check" }, [shortCb, el("span", { text: "Allow under 30s" })]),
    ]),
    gate,
    genBtn,
  ]);

  const card = createGenerationCard({
    onCancel: async (j) => {
      await api.cancel(j.job_id);
      toast("Cancel requested", { type: "info" });
    },
    onRetry: async () => {
      if (!lastBody) return;
      await submit({ ...lastBody, idempotency_key: newIdempotencyKey() });
    },
    onOpen: (j) => navigate("projects", [j.job_id]),
  });

  panel.append(unavailable, form, card.root);
  page.append(panel);
  root.append(page);

  function updateGate() {
    const h = store.health;
    const c = engineCapabilities(store.providers);
    const key = api.getApiKey();
    let reason = "";
    if (!key) reason = "API key required.";
    else if (!h) reason = "Gateway unreachable.";
    else if (!h.generation_available) reason = "generation_available is false.";
    else if (!c.supportsT2V && !c.anyReady) reason = "No ready text-to-video engine (wan or ltx when ready).";

    const ok = !reason;
    unavailable.replaceChildren();
    if (!ok) {
      unavailable.append(emptyState({
        title: "Text → Video unavailable",
        body: reason,
        action: el("button", {
          type: "button",
          className: "ls-btn",
          text: "System status",
          onClick: () => navigate("system"),
        }),
      }));
    }
    genBtn.disabled = !ok;
    genBtn.title = ok ? "Generate" : reason;
    gate.textContent = ok ? `Ready: ${c.readyT2V.map((p) => p.name).join(", ") || "—"}` : "";
  }

  async function submit(body) {
    lastBody = body;
    try {
      const created = await api.createVideo(body);
      toast(`Queued ${abridgeId(created.job_id)}`, { type: "success" });
      store.trackJob(created.job_id);
      poller.watch(created.job_id);
      poller.subscribe(created.job_id, (job) => card.update(job));
      card.update(await api.getVideo(created.job_id));
    } catch (err) {
      toast(err.message || "Create failed", { type: "error" });
    }
  }

  genBtn.addEventListener("click", async () => {
    updateGate();
    if (genBtn.disabled) return;
    const text = prompt.value.trim();
    if (!text) {
      toast("Prompt is required", { type: "error" });
      return;
    }
    const body = {
      prompt: text,
      negative_prompt: neg.value || "",
      duration: Number(duration.value) || 30,
      aspect_ratio: aspect.value,
      quality: quality.value,
      engine: engine.value || "auto",
      audio: audioCb.checked,
      captions: capCb.checked,
      allow_short: shortCb.checked,
      idempotency_key: newIdempotencyKey(),
    };
    if (resolution.value) body.resolution = resolution.value;
    if (seed.value !== "") body.seed = Number(seed.value);
    await submit(body);
  });

  updateGate();
  const unsub = store.subscribe(() => {
    const next = select("engine", engineOptions(), engine.value);
    engine.replaceWith(next);
    engine = next;
    updateGate();
  });

  return () => {
    unsub();
    card.destroy();
  };
}

export default renderTextVideo;

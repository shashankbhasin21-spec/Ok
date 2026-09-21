/**
 * Image → Video — first-class I2V flow.
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
import { createDropzone } from "../components/dropzone.js";
import { createGenerationCard } from "../components/generationCard.js";
import { toast } from "../components/toast.js";

function newIdempotencyKey() {
  return `i2v-${crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
}

export async function renderImageVideo(root, ctx) {
  const { api, store, poller, navigate } = ctx;
  clear(root);

  let uploadedPath = null;
  let lastBody = null;

  const page = el("div", { className: "ls-page" });
  page.append(el("header", { className: "ls-hero ls-hero--compact" }, [
    el("div", { className: "ls-hero__brand", text: "Lumen" }),
    el("h2", { className: "ls-hero__title", text: "Image → Video" }),
    el("p", { className: "ls-hero__lede", text: "Animate a still with open I2V engines — only when they are ready." }),
  ]));

  const unavailable = el("div", {});
  const formHost = el("div", { className: "ls-panel" });
  const layout = el("div", { className: "ls-i2v-layout ls-row" });

  const drop = createDropzone({
    upload: (file) => api.uploadAsset(file),
    onChange: (_f, uploaded) => {
      uploadedPath = uploaded?.path || uploaded?.id || null;
    },
    onClear: () => { uploadedPath = null; },
  });
  drop.root.addEventListener("dropzone-error", (e) => {
    toast(e.detail?.message || "Upload failed", { type: "error", title: "Upload" });
  });

  const prefs = store.prefs || {};
  const prompt = el("textarea", {
    className: "ls-textarea",
    rows: "4",
    placeholder: "Motion prompt — camera push-in, fabric ripple, drifting fog…",
    "aria-label": "Motion prompt",
  });

  function engineOptions() {
    const c = engineCapabilities(store.providers);
    const opts = [{ value: "auto", label: "Auto" }];
    (store.providers || [])
      .filter((p) => (p.tasks || []).includes("image-to-video"))
      .forEach((p) => {
        opts.push({
          value: p.name,
          label: p.ready ? p.name : `${p.name} (not ready)`,
          disabled: !p.ready,
        });
      });
    return opts;
  }

  let engine = select("engine", engineOptions(), "auto");
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
  const quality = select("quality", [
    { value: "draft", label: "Draft" },
    { value: "standard", label: "Standard" },
    { value: "high", label: "High" },
    { value: "max", label: "Max" },
  ], prefs.defaultQuality || "high");
  const neg = el("input", { className: "ls-input", placeholder: "Optional" });
  const seed = el("input", { className: "ls-input", type: "number", placeholder: "Random" });
  const gate = el("div", { className: "ls-composer__gate", role: "status" });
  const genBtn = el("button", { type: "button", className: "ls-btn ls-btn--primary", text: "Generate", disabled: true });

  const formBody = el("div", {}, [
    field("Motion prompt", prompt),
    el("div", { className: "ls-row" }, [
      field("Model", engine),
      field("Aspect", aspect),
      field("Duration (s)", duration),
      field("Quality", quality),
    ]),
    field("Negative prompt", neg),
    field("Seed", seed),
    gate,
    genBtn,
  ]);

  const mediaCol = el("section", { className: "ls-panel" }, [
    el("h3", { text: "Source still" }),
    drop.root,
  ]);
  const formCol = el("section", {}, [unavailable, formHost]);
  formHost.append(el("h3", { text: "Motion" }), formBody);

  const live = el("div", { className: "ls-panel" }, [el("h3", { text: "Generation" })]);
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
  live.append(card.root);

  layout.append(mediaCol, formCol);
  page.append(layout, live);
  root.append(page);

  function refreshEngineSelect() {
    const parent = engine.parentElement;
    const next = select("engine", engineOptions(), engine.value === "auto" || engineOptions().some((o) => o.value === engine.value && !o.disabled) ? engine.value : "auto");
    engine.replaceWith(next);
    engine = next;
    if (parent && !parent.contains(engine)) {
      /* select() creates new node; field wraps it — rebuild field if needed */
    }
  }

  function updateAvailability() {
    const h = store.health;
    const c = engineCapabilities(store.providers);
    const key = api.getApiKey();
    let reason = "";
    if (!key) reason = "API key required.";
    else if (!h) reason = "Gateway unreachable.";
    else if (!h.generation_available) reason = "generation_available is false.";
    else if (!c.supportsI2V) reason = "No provider reports ready=true for image-to-video.";

    const available = !reason;
    unavailable.replaceChildren();
    if (!available) {
      formHost.hidden = true;
      unavailable.append(emptyState({
        title: "Image → Video unavailable",
        body: reason + " Engines are never marked ready from HTTP health alone.",
        action: el("button", {
          type: "button",
          className: "ls-btn",
          text: "View models",
          onClick: () => navigate("models"),
        }),
      }));
    } else {
      formHost.hidden = false;
      gate.textContent = `Ready engines: ${c.readyI2V.map((p) => p.name).join(", ")}`;
    }
    genBtn.disabled = !available;
    genBtn.title = available ? "Generate" : reason;
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
    updateAvailability();
    if (genBtn.disabled) return;
    const text = prompt.value.trim();
    if (!text) {
      toast("Motion prompt is required", { type: "error" });
      return;
    }
    if (!uploadedPath) {
      toast("Add a source still first", { type: "error" });
      return;
    }
    const body = {
      prompt: text,
      negative_prompt: neg.value || "",
      duration: Number(duration.value) || 30,
      aspect_ratio: aspect.value,
      quality: quality.value,
      engine: engine.value || "auto",
      input_image: uploadedPath,
      idempotency_key: newIdempotencyKey(),
      allow_short: Number(duration.value) < 30,
    };
    if (seed.value !== "") body.seed = Number(seed.value);
    await submit(body);
  });

  updateAvailability();
  const unsub = store.subscribe(() => {
    refreshEngineSelect();
    updateAvailability();
  });

  return () => {
    unsub();
    drop.destroy();
    card.destroy();
  };
}

export default renderImageVideo;

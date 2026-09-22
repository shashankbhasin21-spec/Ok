/**
 * Create Studio — three-panel cinematic workspace.
 */

import {
  el,
  clear,
  field,
  select,
  engineCapabilities,
  statusBadge,
  formatDuration,
  abridgeId,
  emptyState,
  downloadBlobFile,
} from "../ui.js";
import { createDropzone } from "../components/dropzone.js";
import { createGenerationCard } from "../components/generationCard.js";
import { createVideoPlayer } from "../components/videoPlayer.js";
import { toast } from "../components/toast.js";

function newIdempotencyKey(prefix = "create") {
  return `${prefix}-${crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
}

export async function renderCreate(root, ctx) {
  const { api, store, poller, navigate } = ctx;
  clear(root);

  let mode = "t2v";
  let uploadedPath = null;
  let lastBody = null;
  const caps = () => engineCapabilities(store.providers);
  const prefs = store.prefs || {};

  const page = el("div", { className: "ls-page ls-studio" });
  const left = el("aside", { className: "ls-studio__controls", "aria-label": "Controls" });
  const center = el("section", { className: "ls-studio__preview", "aria-label": "Preview" });
  const right = el("aside", { className: "ls-studio__inspector", "aria-label": "Inspector" });

  left.append(el("h2", { text: "Create Studio" }));

  const tabs = el("div", { className: "ls-tabs", role: "tablist", "aria-label": "Mode" }, [
    el("button", { type: "button", className: "ls-tabs__btn", role: "tab", "aria-selected": "true", dataset: { mode: "t2v" }, text: "Text → Video" }),
    el("button", { type: "button", className: "ls-tabs__btn", role: "tab", "aria-selected": "false", dataset: { mode: "i2v" }, text: "Image → Video" }),
  ]);
  left.append(tabs);

  const prompt = el("textarea", {
    className: "ls-textarea",
    rows: "5",
    placeholder: "Describe the shot, camera, light, and mood…",
    "aria-label": "Prompt",
  });
  left.append(field("Prompt", prompt));

  const drop = createDropzone({
    upload: (file) => api.uploadAsset(file),
    onChange: (_f, uploaded) => {
      uploadedPath = uploaded?.path || uploaded?.id || null;
    },
    onClear: () => { uploadedPath = null; },
  });
  drop.root.hidden = true;
  drop.root.addEventListener("dropzone-error", (e) => {
    toast(e.detail?.message || "Upload failed", { type: "error", title: "Image upload" });
  });
  left.append(drop.root);

  const engine = select("engine", [
    { value: "auto", label: "Auto (router)" },
    { value: "wan", label: "Wan" },
    { value: "ltx", label: "LTX" },
    { value: "framepack", label: "FramePack" },
  ], prefs.defaultEngine || "auto");

  const aspect = select("aspect_ratio", [
    { value: "9:16", label: "9:16" },
    { value: "16:9", label: "16:9" },
    { value: "1:1", label: "1:1" },
    { value: "4:5", label: "4:5" },
  ], prefs.defaultAspect || "9:16");

  const duration = el("input", {
    className: "ls-input", type: "number", min: "1", max: "600",
    value: String(prefs.defaultDuration || 30), name: "duration",
  });
  const fps = el("input", { className: "ls-input", type: "number", min: "8", max: "60", value: "30", name: "fps" });
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

  left.append(el("div", { className: "ls-row" }, [
    field("Model", engine),
    field("Aspect", aspect),
    field("Duration (s)", duration),
    field("FPS", fps),
    field("Resolution", resolution),
    field("Quality", quality),
  ]));

  const neg = el("input", { className: "ls-input", name: "negative_prompt", placeholder: "Optional" });
  const seed = el("input", { className: "ls-input", type: "number", name: "seed", placeholder: "Random" });
  const audioCb = el("input", { type: "checkbox", name: "audio" });
  const capCb = el("input", { type: "checkbox", name: "captions" });
  const shortCb = el("input", { type: "checkbox", name: "allow_short" });

  left.append(el("details", { className: "ls-details" }, [
    el("summary", { text: "Advanced" }),
    field("Negative prompt", neg),
    field("Seed", seed),
    el("label", { className: "ls-field ls-field--check" }, [audioCb, el("span", { text: "Audio" })]),
    el("label", { className: "ls-field ls-field--check" }, [capCb, el("span", { text: "Captions" })]),
    el("label", { className: "ls-field ls-field--check" }, [shortCb, el("span", { text: "Allow under 30s" })]),
  ]));

  const gate = el("div", { className: "ls-composer__gate", role: "status" });
  const genBtn = el("button", { type: "button", className: "ls-btn ls-btn--primary", text: "Generate", disabled: true });
  left.append(gate, genBtn);

  const player = createVideoPlayer({
    aspectRatio: prefs.defaultAspect || "16:9",
    onDownload: async (id) => {
      if (!id) return;
      try {
        const blob = await api.downloadBlob(id);
        downloadBlobFile(blob, `${id}.mp4`);
      } catch (err) {
        toast(err.message || "Download failed", { type: "error" });
      }
    },
    onRegenerate: async () => {
      if (!lastBody) return;
      await submit({ ...lastBody, idempotency_key: newIdempotencyKey("regen") });
    },
    onDuplicate: () => {
      if (!lastBody) return;
      prompt.value = lastBody.prompt || "";
      toast("Settings restored in controls", { type: "success" });
    },
    onVariation: async () => {
      if (!lastBody) return;
      await submit({
        ...lastBody,
        seed: Math.floor(Math.random() * 1e9),
        idempotency_key: newIdempotencyKey("var"),
      });
    },
  });
  center.append(el("h2", { className: "ls-visually-hidden", text: "Preview" }), player.root);

  const card = createGenerationCard({
    onCancel: async (j) => {
      await api.cancel(j.job_id);
      toast("Cancel requested", { type: "info", title: "Queue" });
    },
    onRetry: async () => {
      if (!lastBody) return;
      await submit({
        ...lastBody,
        idempotency_key: lastBody.idempotency_key || newIdempotencyKey("retry"),
      });
    },
    onOpen: (j) => navigate("projects", [j.job_id]),
  });
  center.append(card.root);

  const inspCaps = el("div", {});
  const inspJob = el("div", {}, [el("p", { className: "ls-field__hint", text: "No job selected" })]);
  right.append(
    el("h2", { text: "Inspector" }),
    el("h3", { text: "Engine capabilities" }),
    inspCaps,
    el("h3", { text: "Job" }),
    inspJob
  );

  page.append(left, center, right);
  root.append(page);

  function paintInspector() {
    const c = caps();
    const name = engine.value;
    inspCaps.replaceChildren();
    if (name === "auto") {
      const ready = mode === "i2v" ? c.readyI2V : c.readyT2V;
      inspCaps.append(
        el("p", { text: "Router picks among ready engines for this task." }),
        ready.length
          ? el("ul", { className: "ls-list" }, ready.map((p) =>
              el("li", { text: `${p.name} · VRAM ≥ ${p.min_vram_gb ?? "—"} GB` })))
          : el("p", { className: "ls-field__hint", text: "No ready engines for this mode." })
      );
      return;
    }
    const p = c.byName[name];
    if (!p) {
      inspCaps.append(emptyState({ title: "Unknown engine", body: "Not in provider catalog." }));
      return;
    }
    inspCaps.append(
      statusBadge(p.ready ? "AVAILABLE" : p.status),
      el("p", { text: `Tasks: ${(p.tasks || []).join(", ") || "—"}` }),
      el("p", { text: `Min VRAM: ${p.min_vram_gb != null ? `${p.min_vram_gb} GB` : "—"}` }),
      el("p", { text: `Installed: ${p.installed ? "yes" : "no"} · Ready: ${p.ready ? "yes" : "no"}` }),
      p.details?.models
        ? el("p", { className: "ls-field__hint", text: `Models: ${(p.details.models || []).join(", ")}` })
        : null
    );
  }

  function updateGate() {
    const h = store.health;
    const c = caps();
    const key = api.getApiKey();
    let reason = "";
    if (!key) reason = "Add an API key in Settings.";
    else if (!h) reason = "Gateway unreachable.";
    else if (!h.generation_available) reason = "generation_available is false — no ready CUDA worker/engine.";
    else if (mode === "i2v" && !c.supportsI2V) reason = "No ready image-to-video engine.";
    else if (mode === "t2v" && !c.supportsT2V && !c.anyReady) reason = "No ready text-to-video engine.";
    genBtn.disabled = Boolean(reason);
    genBtn.title = reason || "Generate";
    gate.textContent = reason || "Ready to generate";
  }

  function setMode(next) {
    mode = next;
    tabs.querySelectorAll(".ls-tabs__btn").forEach((b) => {
      b.setAttribute("aria-selected", b.dataset.mode === mode ? "true" : "false");
    });
    drop.root.hidden = mode !== "i2v";
    // Disable framepack for T2V in select
    [...engine.options].forEach((o) => {
      if (o.value === "framepack") o.disabled = mode === "t2v";
    });
    if (mode === "t2v" && engine.value === "framepack") engine.value = "auto";
    paintInspector();
    updateGate();
  }

  tabs.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-mode]");
    if (btn) setMode(btn.dataset.mode);
  });
  engine.addEventListener("change", paintInspector);

  function paintJob(job) {
    inspJob.replaceChildren();
    if (!job) {
      inspJob.append(el("p", { className: "ls-field__hint", text: "No job selected" }));
      return;
    }
    inspJob.append(
      statusBadge(job.status),
      el("p", { text: `ID: ${abridgeId(job.job_id)}` }),
      el("p", { text: `Engine: ${job.engine || "—"}` }),
      el("p", { text: `Duration: ${formatDuration(job.output_duration ?? job.duration)}` }),
      el("p", { text: `Aspect: ${job.aspect_ratio || "—"}` }),
      el("p", { text: `Ready: ${job.ready ? "yes" : "no"}` }),
      job.qc_status ? el("p", { text: `QC: ${job.qc_status}` }) : null
    );
  }

  async function loadPreview(job) {
    player.clear();
    if (!job?.ready || !job.job_id) return;
    try {
      const blob = await api.downloadBlob(job.job_id);
      await player.loadBlob(blob, { id: job.job_id, ratio: job.aspect_ratio });
    } catch {
      /* preview optional until READY */
    }
  }

  async function submit(body) {
    lastBody = body;
    player.clear();
    try {
      const created = await api.createVideo(body);
      toast(`Queued ${abridgeId(created.job_id)}`, { type: "success", title: "Generation" });
      store.trackJob(created.job_id);
      poller.watch(created.job_id);
      poller.subscribe(created.job_id, async (job) => {
        card.update(job);
        paintJob(job);
        if (job.ready) await loadPreview(job);
      });
      const job = await api.getVideo(created.job_id);
      card.update(job);
      paintJob(job);
    } catch (err) {
      toast(err.message || "Create failed", { type: "error" });
    }
  }

  genBtn.addEventListener("click", async () => {
    updateGate();
    if (genBtn.disabled) {
      toast(gate.textContent || "Unavailable", { type: "error" });
      return;
    }
    const text = prompt.value.trim();
    if (!text) {
      toast("Prompt is required", { type: "error" });
      return;
    }
    if (mode === "i2v" && !uploadedPath) {
      toast("Upload a reference image first", { type: "error", title: "Image required" });
      return;
    }
    const body = {
      prompt: text,
      negative_prompt: neg.value || "",
      duration: Number(duration.value) || 30,
      aspect_ratio: aspect.value,
      fps: Number(fps.value) || 30,
      quality: quality.value,
      engine: engine.value || "auto",
      audio: audioCb.checked,
      captions: capCb.checked,
      allow_short: shortCb.checked,
      idempotency_key: newIdempotencyKey(),
    };
    if (resolution.value) body.resolution = resolution.value;
    if (seed.value !== "") body.seed = Number(seed.value);
    if (mode === "i2v") body.input_image = uploadedPath;
    await submit(body);
  });

  // Prefill from assets reuse
  const prefill = store.createPrefill;
  if (prefill) {
    if (prefill.prompt) prompt.value = prefill.prompt;
    if (prefill.input_image) {
      uploadedPath = prefill.input_image;
      setMode("i2v");
      gate.textContent = `Reusing asset · ${String(prefill.input_image).slice(0, 64)}`;
    }
    if (prefill.aspect_ratio) aspect.value = prefill.aspect_ratio;
    if (prefill.duration != null) duration.value = String(prefill.duration);
    store.createPrefill = null;
  }

  setMode(mode);
  const unsub = store.subscribe(() => updateGate());

  return () => {
    unsub();
    drop.destroy();
    card.destroy();
    player.destroy();
  };
}

export default renderCreate;

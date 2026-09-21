import { el, field, select, engineCapabilities, projectTitle, statusBadge, formatRelative, abridgeId, emptyState } from "../ui.js";
import { tabIndicator } from "../motion.js";
import { createDropzone } from "../components/dropzone.js";
import { createGenerationCard } from "../components/generationCard.js";
import { toast } from "../components/toast.js";

function newIdempotencyKey() {
  return `ui-${crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
}

export default async function renderHome(root, ctx) {
  const { api, store, poller, navigate } = ctx;
  const caps = engineCapabilities(store.providers);
  const prefs = store.prefs;

  let mode = "t2v";
  let uploadedPath = null;
  let lastIdempotency = null;

  const page = el("div", { className: "ls-page" });
  const hero = el("header", { className: "ls-hero" }, [
    el("div", { className: "ls-hero__brand", text: "Lumen" }),
    el("h2", { className: "ls-hero__title", text: "Create something extraordinary" }),
    el("p", { className: "ls-hero__lede", text: "Open-model cinematic generation. Every status, engine, and metric reflects live gateway state." }),
  ]);

  const tabs = el("div", { className: "ls-tabs", role: "tablist", "aria-label": "Creation mode" }, [
    el("button", { type: "button", className: "ls-tabs__btn", role: "tab", "aria-selected": "true", dataset: { mode: "t2v" }, text: "Text → Video" }),
    el("button", { type: "button", className: "ls-tabs__btn", role: "tab", "aria-selected": "false", dataset: { mode: "i2v" }, text: "Image → Video" }),
  ]);
  const indicatorApi = tabIndicator(tabs);

  const prompt = el("textarea", {
    className: "ls-textarea",
    name: "prompt",
    rows: "4",
    placeholder: "A slow push into a rain-soaked neon alley, shallow depth of field, cinematic grain…",
    "aria-label": "Prompt",
  });

  const drop = createDropzone({
    upload: (file) => api.uploadAsset(file),
    onChange: (_f, uploaded) => {
      uploadedPath = uploaded?.path || null;
    },
    onClear: () => { uploadedPath = null; },
  });
  drop.root.hidden = true;
  drop.root.addEventListener("dropzone-error", (e) => {
    toast(e.detail?.message || "Upload failed", { type: "error", title: "Image upload" });
  });

  const engine = select("engine", [
    { value: "auto", label: "Auto" },
    { value: "fast", label: "Fast" },
    { value: "quality", label: "Quality" },
    { value: "low_vram", label: "Low VRAM" },
    { value: "wan", label: "Wan" },
    { value: "ltx", label: "LTX" },
    { value: "framepack", label: "FramePack" },
  ], prefs.defaultEngine || "auto");

  const aspect = select("aspect_ratio", [
    { value: "9:16", label: "9:16 Vertical" },
    { value: "16:9", label: "16:9 Landscape" },
    { value: "1:1", label: "1:1 Square" },
    { value: "4:5", label: "4:5" },
  ], prefs.defaultAspect || "9:16");

  const duration = el("input", {
    className: "ls-input",
    type: "number",
    name: "duration",
    min: "1",
    max: "600",
    value: String(prefs.defaultDuration || 30),
  });

  const resolution = el("input", {
    className: "ls-input",
    type: "text",
    name: "resolution",
    placeholder: "Auto from aspect",
  });

  const quality = select("quality", [
    { value: "draft", label: "Draft" },
    { value: "standard", label: "Standard" },
    { value: "high", label: "High" },
    { value: "max", label: "Max" },
  ], prefs.defaultQuality || "high");

  const advanced = el("div", { hidden: true, className: "ls-row" }, [
    field("Negative prompt", el("input", { className: "ls-input", name: "negative_prompt", placeholder: "Optional" })),
    field("Seed", el("input", { className: "ls-input", type: "number", name: "seed", placeholder: "Optional" })),
    field("Quality", quality),
  ]);

  const genBtn = el("button", { type: "submit", className: "ls-btn ls-btn--primary ls-btn--magnetic", text: "Generate" });
  const gate = el("div", { className: "ls-composer__gate" });
  const liveHost = el("div", {});

  function updateGate() {
    const h = store.health;
    const key = api.getApiKey();
    let reason = "";
    if (!key) reason = "Add an API key in Settings before generating.";
    else if (!h) reason = "Gateway unreachable — generation is blocked.";
    else if (!h.generation_available) {
      reason = h.gpu_worker_available
        ? "No ready generation engine. Check Models / System — worker health alone is not enough."
        : "No compatible GPU worker connected. Generation is unavailable.";
    } else if (mode === "i2v" && !caps.supportsI2V) {
      reason = "Image → Video is unavailable: no ready engine advertises image-to-video.";
    } else if (mode === "t2v" && !caps.supportsT2V && !caps.anyReady) {
      reason = "Text → Video unavailable: no ready text-to-video engine.";
    }
    gate.textContent = reason;
    genBtn.disabled = Boolean(reason);
    genBtn.title = reason || "Queue generation";
  }

  tabs.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-mode]");
    if (!btn) return;
    mode = btn.dataset.mode;
    tabs.querySelectorAll(".ls-tabs__btn").forEach((b) => b.setAttribute("aria-selected", b === btn ? "true" : "false"));
    indicatorApi.update();
    drop.root.hidden = mode !== "i2v";
    updateGate();
  });

  const form = el("form", { className: "ls-panel ls-composer", onSubmit: async (ev) => {
    ev.preventDefault();
    updateGate();
    if (genBtn.disabled) {
      toast(gate.textContent || "Generation unavailable", { type: "error", title: "Cannot generate" });
      return;
    }
    if (mode === "i2v" && !uploadedPath) {
      toast("Upload a reference image first.", { type: "error", title: "Image required" });
      return;
    }
    const fd = new FormData(form);
    lastIdempotency = newIdempotencyKey();
    const body = {
      prompt: String(fd.get("prompt") || "").trim(),
      duration: Number(fd.get("duration") || 30),
      aspect_ratio: fd.get("aspect_ratio"),
      quality: fd.get("quality") || prefs.defaultQuality || "high",
      engine: fd.get("engine") || "auto",
      negative_prompt: String(fd.get("negative_prompt") || ""),
      idempotency_key: lastIdempotency,
    };
    const res = String(fd.get("resolution") || "").trim();
    if (res) body.resolution = res;
    const seed = fd.get("seed");
    if (seed !== "" && seed != null) body.seed = Number(seed);
    if (mode === "i2v") body.input_image = uploadedPath;

    genBtn.classList.add("is-loading");
    genBtn.textContent = "Queuing…";
    try {
      const created = await api.createVideo(body);
      toast(`Queued ${created.job_id}`, { type: "success", title: "Generation started" });
      const card = createGenerationCard({
        onCancel: async (j) => {
          await api.cancel(j.job_id);
          toast("Cancel requested", { title: "Queue" });
        },
        onRetry: async () => {
          const retryBody = { ...body, idempotency_key: newIdempotencyKey() };
          const again = await api.createVideo(retryBody);
          poller.watch(again.job_id);
          card.update(await api.getVideo(again.job_id));
        },
        onOpen: (j) => navigate("projects", [j.job_id]),
      });
      liveHost.replaceChildren(card.root);
      poller.watch(created.job_id);
      poller.subscribe(created.job_id, (job) => card.update(job));
      card.update(await api.getVideo(created.job_id));
      store.trackJob(created.job_id);
    } catch (err) {
      toast(err.message || String(err), { type: "error", title: "Generation failed to queue" });
    } finally {
      genBtn.classList.remove("is-loading");
      genBtn.textContent = "Generate";
      updateGate();
    }
  } }, [
    tabs,
    field("Prompt", prompt),
    drop.root,
    el("div", { className: "ls-row" }, [
      field("Model / engine", engine),
      field("Aspect ratio", aspect),
      field("Duration (s)", duration),
      field("Resolution", resolution),
    ]),
    el("button", {
      type: "button",
      className: "ls-btn ls-btn--ghost ls-btn--sm",
      text: "Advanced controls",
      onClick: () => { advanced.hidden = !advanced.hidden; },
    }),
    advanced,
    el("div", { className: "ls-composer__footer" }, [gate, genBtn]),
  ]);

  const recentProjects = el("div", { className: "ls-grid" });
  const recentGens = el("div", { className: "ls-queue-list" });
  const queueBox = el("div", { className: "ls-queue-list" });
  const enginesBox = el("div", { className: "ls-grid ls-grid--engines" });

  function renderLists() {
    const jobs = store.jobs || [];
    recentProjects.replaceChildren();
    if (!jobs.length) {
      recentProjects.append(emptyState({
        title: "No projects yet",
        body: "Your generations will appear here with real status from the gateway.",
        icon: "▣",
      }));
    } else {
      jobs.slice(0, 6).forEach((j) => {
        recentProjects.append(el("button", {
          type: "button",
          className: "ls-media-card",
          onClick: () => navigate("projects", [j.job_id]),
        }, [
          el("div", { className: "ls-media-card__thumb" }, [
            el("div", { className: "ls-media-card__thumb-fallback", text: (j.aspect_ratio || "▣") }),
          ]),
          el("div", { className: "ls-media-card__body" }, [
            el("div", { className: "ls-media-card__title", text: projectTitle(j) }),
            el("div", { className: "ls-media-card__meta" }, [
              statusBadge(j.status),
              el("span", { text: j.engine || "—" }),
              el("span", { text: formatRelative(j.created_at) }),
            ]),
          ]),
        ]));
      });
    }

    recentGens.replaceChildren();
    jobs.slice(0, 5).forEach((j) => {
      recentGens.append(el("div", { className: "ls-queue-row" }, [
        el("div", { className: "ls-queue-row__thumb", text: abridgeId(j.job_id, 4) }),
        el("div", {}, [
          el("div", { className: "ls-queue-row__title", text: projectTitle(j) }),
          el("div", { className: "ls-queue-row__meta", text: `${j.status} · ${j.engine || "—"} · ${formatRelative(j.created_at)}` }),
        ]),
        statusBadge(j.status),
      ]));
    });
    if (!jobs.length) recentGens.append(el("p", { className: "ls-field__hint", text: "No generations yet." }));

    const active = jobs.filter((j) => store.isActive(j.status));
    queueBox.replaceChildren();
    if (!active.length) queueBox.append(el("p", { className: "ls-field__hint", text: "Queue is empty." }));
    active.slice(0, 6).forEach((j) => {
      queueBox.append(el("div", { className: "ls-queue-row" }, [
        el("div", { className: "ls-queue-row__thumb", text: "…" }),
        el("div", {}, [
          el("div", { className: "ls-queue-row__title", text: abridgeId(j.job_id) }),
          el("div", { className: "ls-queue-row__meta", text: `${j.status} · ${j.engine || "routing"}` }),
        ]),
        statusBadge(j.status),
      ]));
    });

    enginesBox.replaceChildren();
    const providers = store.providers || [];
    if (!providers.length) {
      enginesBox.append(emptyState({
        title: api.getApiKey() ? "No provider data" : "API key required",
        body: api.getApiKey()
          ? "Providers could not be loaded from the gateway."
          : "Set your API key in Settings to load configured engines.",
      }));
    } else {
      providers.forEach((p) => {
        enginesBox.append(el("div", { className: "ls-panel ls-engine" }, [
          el("div", { className: "ls-engine__top" }, [
            el("div", { className: "ls-engine__name", text: p.name }),
            statusBadge(p.status),
          ]),
          el("div", { className: "ls-engine__tasks" }, (p.tasks || []).map((t) => el("span", { className: "ls-badge", text: t }))),
          el("div", { className: "ls-engine__meta" }, [
            el("span", { text: p.ready ? "Ready for generation" : "Not ready" }),
            el("span", { text: p.min_vram_gb != null ? `Min VRAM ~${p.min_vram_gb} GB` : "VRAM n/a" }),
          ]),
        ]));
      });
    }
  }

  page.append(
    hero,
    form,
    liveHost,
    el("div", { className: "ls-home-sections" }, [
      el("section", {}, [
        el("div", { className: "ls-section-hd" }, [
          el("div", {}, [el("h2", { text: "Recent Projects" }), el("p", { text: "From stored jobs" })]),
          el("button", { type: "button", className: "ls-btn ls-btn--ghost ls-btn--sm", text: "View all", onClick: () => navigate("projects") }),
        ]),
        recentProjects,
      ]),
      el("section", {}, [
        el("div", { className: "ls-section-hd" }, [
          el("div", {}, [el("h2", { text: "Generation Queue" }), el("p", { text: "Active pipeline stages" })]),
          el("button", { type: "button", className: "ls-btn ls-btn--ghost ls-btn--sm", text: "Open queue", onClick: () => navigate("queue") }),
        ]),
        queueBox,
        el("div", { className: "ls-section-hd", style: "margin-top:1.5rem" }, [
          el("div", {}, [el("h2", { text: "Recent Generations" })]),
        ]),
        recentGens,
      ]),
    ]),
    el("section", {}, [
      el("div", { className: "ls-section-hd" }, [
        el("div", {}, [el("h2", { text: "Available Engines" }), el("p", { text: "Configured providers — readiness is capability-gated" })]),
        el("button", { type: "button", className: "ls-btn ls-btn--ghost ls-btn--sm", text: "Models", onClick: () => navigate("models") }),
      ]),
      enginesBox,
    ]),
  );

  root.append(page);
  renderLists();
  updateGate();
  const unsub = store.subscribe(() => {
    renderLists();
    updateGate();
  });

  return () => {
    unsub();
    indicatorApi.destroy?.();
    drop.destroy();
  };
}

/** View renderers for Vera Studio. */

import * as api from "./api.js";
import {
  ENGINE_META,
  STAGES,
  anyReadyFor,
  capsFor,
  humanFailure,
  isProviderReady,
  providerStatusLabel,
  stageIndex,
} from "./capabilities.js";
import {
  abbreviateId,
  escapeHtml,
  formatDate,
  formatElapsed,
  magnetic,
  staggerIn,
} from "./motion.js";
import { mountPlayer } from "./player.js";
import {
  forgetActiveJob,
  loadDraft,
  loadSettings,
  patch,
  rememberActiveJob,
  saveDraft,
  saveSettings,
  setGalleryView,
  state,
} from "./store.js";

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

let playerHandle = null;
let objectUrls = [];

function revokeUrls() {
  objectUrls.forEach((u) => URL.revokeObjectURL(u));
  objectUrls = [];
  if (playerHandle) {
    playerHandle.destroy();
    playerHandle = null;
  }
}

function stageTrack(status) {
  if (status === "FAILED") {
    return `<div class="stage-track"><span class="stage-pill failed">FAILED</span></div>`;
  }
  if (status === "CANCELLED") {
    return `<div class="stage-track"><span class="stage-pill failed">CANCELLED</span></div>`;
  }
  const idx = stageIndex(status);
  return `<div class="stage-track">${STAGES.map((s, i) => {
    let cls = "stage-pill";
    if (status === "COMPLETED" || i < idx) cls += " done";
    else if (i === idx) cls += " current";
    return `<span class="${cls}">${s.replaceAll("_", " ")}</span>`;
  }).join("")}</div>`;
}

function engineOptions(selected, { mode = "t2v", readiness, providers } = {}) {
  const ready = new Set(readiness?.ready_engines || []);
  const modes = ["auto", "fast", "quality", "low_vram", "wan", "ltx", "framepack", "cpu_assembly"];
  return modes
    .map((name) => {
      const meta = capsFor(name);
      if (mode === "i2v" && !meta.i2v) return "";
      if (mode === "t2v" && name !== "auto" && name !== "fast" && name !== "quality" && name !== "low_vram" && !meta.t2v)
        return "";
      const isConcrete = ["wan", "ltx", "framepack", "cpu_assembly"].includes(name);
      const unavailable = isConcrete && !ready.has(name);
      const label = unavailable ? `${meta.label} (not ready)` : meta.label;
      return `<option value="${name}" ${selected === name ? "selected" : ""} ${
        unavailable ? "disabled" : ""
      }>${escapeHtml(label)}</option>`;
    })
    .join("");
}

function controlVisibility(engine) {
  const c = capsFor(engine);
  return {
    negative: !!c.negative,
    seed: !!c.seed,
    motion: !!c.motion,
    camera: !!c.camera,
    note: c.assembly
      ? "CPU Assembly produces real MP4 via FFmpeg still motion — not diffusion generation."
      : c.diffusion
        ? "Requires a ready CUDA worker with installed weights."
        : "",
  };
}

export function renderHome(root, ctx) {
  const settings = loadSettings();
  const draft = loadDraft() || {};
  const ready = anyReadyFor("t2v", state.readiness, state.providers);
  const jobs = state.jobs || [];
  const recent = jobs.slice(0, 6);
  const active = jobs.filter((j) => ACTIVE.has(j.status)).slice(0, 6);
  const engines = state.providers || [];

  root.innerHTML = `
    <section class="home-hero">
      <h2 class="display">Create something extraordinary</h2>
      <p class="lede">Compose a cinematic prompt, choose a ready engine, and generate a real playable MP4.</p>
    </section>
    <section class="composer" id="homeComposer">
      <div class="mode-tabs" role="tablist" aria-label="Creation mode">
        <span class="mode-indicator" id="modeIndicator"></span>
        <button type="button" class="mode-tab active" data-mode="t2v" role="tab" aria-selected="true">Text → Video</button>
        <button type="button" class="mode-tab" data-mode="i2v" role="tab" aria-selected="false">Image → Video</button>
      </div>
      <form id="homeForm">
        <input type="hidden" name="mode" value="t2v" />
        <div class="composer-grid">
          <div>
            <label class="field">
              <span>Prompt</span>
              <textarea name="prompt" required placeholder="A cinematic technology scene…">${escapeHtml(
                draft.prompt || ""
              )}</textarea>
            </label>
            <div class="i2v-slot" hidden>
              <div class="dropzone" id="homeDrop" tabindex="0">
                <p>Drop a reference image, or browse</p>
                <input type="file" id="homeImage" accept="image/jpeg,image/png,image/webp" hidden />
                <div class="drop-actions">
                  <button type="button" class="btn btn-sm" data-browse>Browse</button>
                  <button type="button" class="btn btn-sm btn-ghost" data-clear hidden>Remove</button>
                </div>
                <img id="homePreview" alt="" hidden />
              </div>
              <input type="hidden" name="input_image" value="" />
            </div>
          </div>
          <div>
            <label class="field"><span>Engine</span>
              <select name="engine">${engineOptions(draft.engine || settings.defaultEngine, {
                mode: "t2v",
                readiness: state.readiness,
                providers: state.providers,
              })}</select>
            </label>
            <label class="field"><span>Aspect</span>
              <select name="aspect_ratio">
                ${["9:16", "16:9", "1:1", "4:5"]
                  .map(
                    (a) =>
                      `<option value="${a}" ${
                        (draft.aspect_ratio || settings.defaultAspect) === a ? "selected" : ""
                      }>${a}</option>`
                  )
                  .join("")}
              </select>
            </label>
            <label class="field"><span>Duration (seconds)</span>
              <input name="duration" type="number" min="1" max="120" value="${
                draft.duration || settings.defaultDuration
              }" />
            </label>
            <label class="field"><span>Quality</span>
              <select name="quality">
                ${["draft", "standard", "high", "max"]
                  .map(
                    (q) =>
                      `<option ${
                        (draft.quality || settings.defaultQuality) === q ? "selected" : ""
                      }>${q}</option>`
                  )
                  .join("")}
              </select>
            </label>
          </div>
        </div>
        <div class="composer-actions">
          <button type="submit" class="btn btn-primary" id="homeGenerate" ${
            ready ? "" : "disabled"
          }>Generate</button>
          <button type="button" class="btn btn-ghost" data-nav="create">Open full studio</button>
          <span class="hint" id="homeReadyHint">${
            ready
              ? `Ready engines: ${(state.readiness?.ready_engines || []).join(", ") || "—"}`
              : "No generation engine is ready. Check System."
          }</span>
        </div>
      </form>
    </section>

    <div class="section-head"><h2>Studio pulse</h2></div>
    <div class="rail-grid">
      <div class="rail">
        <h3>Recent generations</h3>
        ${
          recent.length
            ? recent
                .map(
                  (j) => `
            <button type="button" class="gen-card" style="width:100%;margin-bottom:0.45rem" data-open-job="${j.job_id}">
              <div class="gen-card-top">
                <strong style="font-size:0.85rem">${escapeHtml((j.prompt || "").slice(0, 48))}</strong>
                <span class="status-tag ${escapeHtml(j.status)}">${escapeHtml(j.status)}</span>
              </div>
              <div class="gen-meta">${escapeHtml(j.engine || "—")} · ${escapeHtml(
                    abbreviateId(j.job_id)
                  )}</div>
            </button>`
                )
                .join("")
            : `<div class="empty">No generations yet</div>`
        }
      </div>
      <div class="rail">
        <h3>Generation queue</h3>
        ${
          active.length
            ? active
                .map(
                  (j) =>
                    `<div class="gen-meta" style="margin-bottom:0.45rem">${escapeHtml(
                      j.status
                    )} · ${escapeHtml(abbreviateId(j.job_id))}</div>`
                )
                .join("")
            : `<div class="empty">Queue is clear</div>`
        }
      </div>
      <div class="rail">
        <h3>Available engines</h3>
        ${
          engines.length
            ? engines
                .map(
                  (p) =>
                    `<div class="gen-meta" style="margin-bottom:0.35rem"><strong>${escapeHtml(
                      p.name
                    )}</strong> · ${escapeHtml(providerStatusLabel(p.status))}</div>`
                )
                .join("")
            : `<div class="empty">Sign in to load engines</div>`
        }
      </div>
      <div class="rail">
        <h3>Projects</h3>
        <p class="hint">Completed assets appear in Projects & Assets.</p>
        <button type="button" class="btn btn-sm" data-nav="projects" style="margin-top:0.75rem">Browse</button>
      </div>
    </div>
  `;

  wireComposer(root.querySelector("#homeComposer"), ctx, { compact: true });
  const genBtn = root.querySelector("#homeGenerate");
  if (genBtn) magnetic(genBtn);
  staggerIn(root.querySelectorAll(".rail, .composer"));
}

export function renderStudio(root, ctx, { mode = "create" } = {}) {
  const settings = loadSettings();
  const draft = loadDraft() || {};
  const initialMode = mode === "i2v" ? "i2v" : mode === "t2v" ? "t2v" : draft.mode || "t2v";
  const engine = draft.engine || settings.defaultEngine;
  const vis = controlVisibility(engine);
  const ready =
    initialMode === "i2v"
      ? anyReadyFor("i2v", state.readiness, state.providers)
      : anyReadyFor("t2v", state.readiness, state.providers);

  root.innerHTML = `
    <div class="studio" id="studio">
      <aside class="panel-col panel-pad" aria-label="Creation controls">
        <p class="panel-title">Create</p>
        <form id="studioForm">
          <input type="hidden" name="mode" value="${initialMode}" />
          <div class="mode-tabs" style="width:100%;display:grid;grid-template-columns:1fr 1fr">
            <span class="mode-indicator" id="modeIndicator" style="width:calc(50% - 0.22rem)"></span>
            <button type="button" class="mode-tab ${
              initialMode === "t2v" ? "active" : ""
            }" data-mode="t2v">Text → Video</button>
            <button type="button" class="mode-tab ${
              initialMode === "i2v" ? "active" : ""
            }" data-mode="i2v">Image → Video</button>
          </div>
          <label class="field"><span>Prompt</span>
            <textarea name="prompt" required placeholder="Describe the shot…">${escapeHtml(
              draft.prompt || ""
            )}</textarea>
          </label>
          <div class="i2v-slot" ${initialMode === "i2v" ? "" : "hidden"}>
            <div class="dropzone" id="studioDrop" tabindex="0">
              <p>Reference image</p>
              <input type="file" id="studioImage" accept="image/jpeg,image/png,image/webp" hidden />
              <div class="drop-actions">
                <button type="button" class="btn btn-sm" data-browse>Upload</button>
                <button type="button" class="btn btn-sm btn-ghost" data-clear hidden>Remove</button>
              </div>
              <img id="studioPreview" alt="" hidden />
            </div>
            <input type="hidden" name="input_image" value="${escapeHtml(draft.input_image || "")}" />
          </div>
          <label class="field control-negative" ${vis.negative ? "" : "hidden"}><span>Negative prompt</span>
            <textarea name="negative_prompt" rows="2">${escapeHtml(draft.negative_prompt || "")}</textarea>
          </label>
          <label class="field"><span>Engine</span>
            <select name="engine">${engineOptions(engine, {
              mode: initialMode,
              readiness: state.readiness,
              providers: state.providers,
            })}</select>
          </label>
          <p class="hint engine-note">${escapeHtml(vis.note)}</p>
          <label class="field"><span>Aspect ratio</span>
            <select name="aspect_ratio">
              ${["9:16", "16:9", "1:1", "4:5"]
                .map(
                  (a) =>
                    `<option value="${a}" ${
                      (draft.aspect_ratio || settings.defaultAspect) === a ? "selected" : ""
                    }>${a}</option>`
                )
                .join("")}
            </select>
          </label>
          <label class="field"><span>Duration</span>
            <input name="duration" type="number" min="1" max="120" value="${
              draft.duration || settings.defaultDuration
            }" />
          </label>
          <label class="field"><span>Resolution</span>
            <select name="resolution">
              <option value="">Engine default</option>
              <option value="720x1280">720×1280</option>
              <option value="1080x1920">1080×1920</option>
              <option value="1280x720">1280×720</option>
              <option value="1920x1080">1920×1080</option>
            </select>
          </label>
          <label class="field"><span>Quality</span>
            <select name="quality">
              ${["draft", "standard", "high", "max"]
                .map(
                  (q) =>
                    `<option ${
                      (draft.quality || settings.defaultQuality) === q ? "selected" : ""
                    }>${q}</option>`
                )
                .join("")}
            </select>
          </label>
          <label class="field control-seed" ${vis.seed ? "" : "hidden"}><span>Seed</span>
            <input name="seed" type="number" placeholder="optional" value="${
              draft.seed ?? ""
            }" />
          </label>
          <details style="margin:0.5rem 0 0.9rem">
            <summary class="muted" style="cursor:pointer">Advanced</summary>
            <label class="field"><span>FPS</span><input name="fps" type="number" min="8" max="60" value="${
              draft.fps || 24
            }" /></label>
            <label class="field"><span>Transition</span>
              <select name="transition">
                <option value="none">None</option>
                <option value="crossfade">Crossfade</option>
                <option value="fade">Fade</option>
              </select>
            </label>
            <div class="toggle-row"><span>Allow short (&lt;30s)</span><button type="button" class="switch on" data-switch="allow_short" aria-pressed="true"></button></div>
            <div class="toggle-row"><span>Audio</span><button type="button" class="switch" data-switch="audio" aria-pressed="false"></button></div>
            <div class="toggle-row"><span>Captions</span><button type="button" class="switch" data-switch="captions" aria-pressed="false"></button></div>
            <input type="hidden" name="allow_short" value="true" />
            <input type="hidden" name="audio" value="false" />
            <input type="hidden" name="captions" value="false" />
          </details>
          <button type="submit" class="btn btn-primary btn-block" id="studioGenerate" ${
            ready ? "" : "disabled"
          }>Generate</button>
          <p class="hint" id="studioReadyHint">${
            ready ? "Engine capacity looks ready for this mode." : "No compatible ready engine for this mode."
          }</p>
        </form>
      </aside>

      <section class="panel-col preview-canvas" aria-label="Preview">
        <p class="panel-title" style="padding:1rem 1rem 0">Preview</p>
        <div class="preview-stage">
          <div class="aspect-frame" data-ar="${draft.aspect_ratio || settings.defaultAspect}" id="aspectFrame">
            <div class="placeholder" id="previewPlaceholder">
              <strong>Your frame</strong>
              <span>Generate to fill this canvas with a real MP4.</span>
            </div>
            <div id="previewMount" hidden></div>
          </div>
        </div>
        <div id="liveProgress" class="panel-pad"></div>
      </section>

      <aside class="panel-col panel-pad" aria-label="Inspector">
        <p class="panel-title">Inspector</p>
        <div id="inspector">
          <p class="hint">Submit a generation to inspect live status, routing, and output.</p>
        </div>
      </aside>
    </div>
  `;

  wireComposer(root.querySelector("#studio"), ctx, { studio: true });
  syncModeIndicator(root, initialMode);
  const genBtn = root.querySelector("#studioGenerate");
  if (genBtn) magnetic(genBtn);
}

function syncModeIndicator(root, mode) {
  const ind = root.querySelector("#modeIndicator");
  const tabs = [...root.querySelectorAll(".mode-tab")];
  tabs.forEach((t) => {
    const on = t.dataset.mode === mode;
    t.classList.toggle("active", on);
    t.setAttribute("aria-selected", on ? "true" : "false");
  });
  if (ind) {
    const i = mode === "i2v" ? 1 : 0;
    ind.style.transform = `translateX(${i * 100}%)`;
  }
  root.querySelectorAll(".i2v-slot").forEach((el) => {
    el.hidden = mode !== "i2v";
  });
  const form = root.querySelector("form");
  if (form?.elements.mode) form.elements.mode.value = mode;
}

function wireComposer(root, ctx, { studio = false, compact = false } = {}) {
  if (!root) return;
  const form = root.querySelector("form");
  if (!form) return;

  let uploadedPath = form.elements.input_image?.value || "";
  let objectUrl = null;

  root.querySelectorAll(".mode-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mode = btn.dataset.mode;
      syncModeIndicator(root.closest(".studio, .composer") || root, mode);
      const engineSel = form.elements.engine;
      if (engineSel) {
        engineSel.innerHTML = engineOptions(engineSel.value, {
          mode,
          readiness: state.readiness,
          providers: state.providers,
        });
      }
      updateReadyHint(form, mode);
    });
  });

  form.elements.engine?.addEventListener("change", () => {
    const vis = controlVisibility(form.elements.engine.value);
    root.querySelectorAll(".control-negative").forEach((el) => (el.hidden = !vis.negative));
    root.querySelectorAll(".control-seed").forEach((el) => (el.hidden = !vis.seed));
    const note = root.querySelector(".engine-note");
    if (note) note.textContent = vis.note || "";
  });

  form.elements.aspect_ratio?.addEventListener("change", () => {
    const frame = document.getElementById("aspectFrame");
    if (frame) frame.dataset.ar = form.elements.aspect_ratio.value;
  });

  root.querySelectorAll("[data-switch]").forEach((sw) => {
    sw.addEventListener("click", () => {
      const on = !sw.classList.contains("on");
      sw.classList.toggle("on", on);
      sw.setAttribute("aria-pressed", on ? "true" : "false");
      const name = sw.dataset.switch;
      if (form.elements[name]) form.elements[name].value = on ? "true" : "false";
    });
  });

  const drop = root.querySelector(".dropzone");
  const fileInput = root.querySelector('input[type="file"]');
  const preview = root.querySelector("img");
  const clearBtn = root.querySelector("[data-clear]");

  const setPreview = (file, path) => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(file);
    if (preview) {
      preview.src = objectUrl;
      preview.hidden = false;
    }
    uploadedPath = path;
    if (form.elements.input_image) form.elements.input_image.value = path;
    if (clearBtn) clearBtn.hidden = false;
  };

  drop?.querySelector("[data-browse]")?.addEventListener("click", () => fileInput?.click());
  clearBtn?.addEventListener("click", () => {
    uploadedPath = "";
    if (form.elements.input_image) form.elements.input_image.value = "";
    if (preview) {
      preview.hidden = true;
      preview.removeAttribute("src");
    }
    clearBtn.hidden = true;
    if (fileInput) fileInput.value = "";
  });

  const handleFile = async (file) => {
    if (!file) return;
    try {
      ctx.toast({ title: "Uploading image…", tone: "info" });
      const res = await api.uploadImage(file);
      setPreview(file, res.path);
      ctx.toast({ title: "Image ready", tone: "success" });
    } catch (err) {
      ctx.toast({ title: "Upload failed", body: err.message, tone: "error" });
    }
  };

  fileInput?.addEventListener("change", () => handleFile(fileInput.files?.[0]));
  ["dragenter", "dragover"].forEach((ev) =>
    drop?.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.add("drag");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    drop?.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.remove("drag");
    })
  );
  drop?.addEventListener("drop", (e) => handleFile(e.dataTransfer?.files?.[0]));

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (state.submitting) return;
    const mode = form.elements.mode.value || "t2v";
    if (mode === "i2v" && !uploadedPath && !form.elements.input_image.value) {
      ctx.toast({
        title: "Reference image required",
        body: "Image → Video needs an uploaded still.",
        tone: "error",
      });
      return;
    }
    if (!anyReadyFor(mode, state.readiness, state.providers)) {
      ctx.toast({
        title: "Generation unavailable",
        body: "No compatible engine is ready for this mode.",
        tone: "error",
      });
      return;
    }

    const body = collectBody(form, uploadedPath);
    saveDraft({ ...body, mode, input_image: uploadedPath });
    await ctx.submitGeneration(body, { mode, studio });
  });
}

function collectBody(form, uploadedPath) {
  const fd = new FormData(form);
  const body = {
    prompt: String(fd.get("prompt") || "").trim(),
    engine: fd.get("engine") || "auto",
    aspect_ratio: fd.get("aspect_ratio") || "9:16",
    duration: Number(fd.get("duration") || 5),
    quality: fd.get("quality") || "draft",
    negative_prompt: String(fd.get("negative_prompt") || ""),
    allow_short: String(fd.get("allow_short") || "true") === "true",
    audio: String(fd.get("audio") || "false") === "true",
    captions: String(fd.get("captions") || "false") === "true",
    transition: fd.get("transition") || "none",
    fps: Number(fd.get("fps") || 24),
    idempotency_key: `vera-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
  };
  const seed = fd.get("seed");
  if (seed !== "" && seed != null) body.seed = Number(seed);
  const res = fd.get("resolution");
  if (res) body.resolution = String(res);
  const img = uploadedPath || fd.get("input_image");
  if (img) body.input_image = String(img);
  return body;
}

function updateReadyHint(form, mode) {
  const ready = anyReadyFor(mode, state.readiness, state.providers);
  const hint = document.getElementById("studioReadyHint") || document.getElementById("homeReadyHint");
  const btn = form.querySelector('button[type="submit"]');
  if (btn) btn.disabled = !ready || state.submitting;
  if (hint) {
    hint.textContent = ready
      ? `Ready: ${(state.readiness?.ready_engines || []).join(", ")}`
      : "No compatible ready engine for this mode.";
  }
}

export function updateLiveProgress(job, ctx) {
  const live = document.getElementById("liveProgress");
  const inspector = document.getElementById("inspector");
  const placeholder = document.getElementById("previewPlaceholder");
  const mount = document.getElementById("previewMount");
  if (!job) return;

  const started = job.started_at ? new Date(job.started_at).getTime() : Date.now();
  const elapsed = formatElapsed(Date.now() - started);

  if (live) {
    live.innerHTML = `
      <div class="gen-card">
        <div class="gen-card-top">
          <div>
            <strong>${escapeHtml(abbreviateId(job.job_id))}</strong>
            <div class="gen-meta">Elapsed ${escapeHtml(elapsed)} · ${escapeHtml(
              job.engine || "routing…"
            )}</div>
          </div>
          <span class="status-tag ${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
        </div>
        ${stageTrack(job.status)}
      </div>
    `;
  }

  if (inspector) {
    if (job.status === "FAILED") {
      const fail = humanFailure(job);
      inspector.innerHTML = `
        <h3 style="margin:0 0 0.4rem">${escapeHtml(fail.title)}</h3>
        <p class="lede" style="margin-top:0">${escapeHtml(fail.summary)}</p>
        <details class="details-box">
          <summary>Technical details</summary>
          <div>Job: ${escapeHtml(job.job_id)}</div>
          <div>Engine: ${escapeHtml(job.engine || "—")}</div>
          <div>Category: ${escapeHtml(fail.category)}</div>
          <div>Time: ${escapeHtml(formatDate(job.completed_at || job.created_at))}</div>
          <div style="margin-top:0.4rem">${escapeHtml(fail.detail)}</div>
        </details>
        <div style="display:flex;gap:0.5rem;margin-top:0.85rem;flex-wrap:wrap">
          <button type="button" class="btn btn-sm" data-retry>Retry safely</button>
          <button type="button" class="btn btn-sm btn-ghost" data-nav="system">System status</button>
        </div>
      `;
      inspector.querySelector("[data-retry]")?.addEventListener("click", () => {
        const draft = loadDraft() || {};
        ctx.submitGeneration(
          {
            ...draft,
            prompt: job.prompt,
            engine: job.engine_requested || draft.engine || "auto",
            aspect_ratio: job.aspect_ratio,
            duration: job.duration,
            idempotency_key: `vera-retry-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          },
          { studio: true }
        );
      });
    } else {
      inspector.innerHTML = `
        <div class="stat" style="margin-bottom:0.75rem">
          <div class="label">Status</div>
          <div class="value" style="font-size:1.4rem">${escapeHtml(job.status)}</div>
        </div>
        <div class="gen-meta">Engine · ${escapeHtml(job.engine || "—")}</div>
        <div class="gen-meta">Model · ${escapeHtml(job.model || "—")}</div>
        <div class="gen-meta">Created · ${escapeHtml(formatDate(job.created_at))}</div>
        ${
          ACTIVE.has(job.status)
            ? `<button type="button" class="btn btn-sm btn-danger" style="margin-top:0.85rem" data-cancel>Cancel</button>`
            : ""
        }
        ${
          job.ready
            ? `<button type="button" class="btn btn-sm btn-primary" style="margin-top:0.85rem" data-open-job="${job.job_id}">Open result</button>`
            : ""
        }
      `;
      inspector.querySelector("[data-cancel]")?.addEventListener("click", async () => {
        try {
          await api.cancelJob(job.job_id);
          ctx.toast({ title: "Cancel requested", tone: "info" });
          ctx.refreshJobs();
        } catch (err) {
          ctx.toast({ title: "Cancel failed", body: err.message, tone: "error" });
        }
      });
    }
  }

  if (job.ready && mount && placeholder) {
    placeholder.hidden = true;
    mount.hidden = false;
    loadPreview(mount, job, ctx);
  }
}

async function loadPreview(mount, job, ctx) {
  if (mount.dataset.jobId === job.job_id && playerHandle) return;
  revokeUrls();
  mount.dataset.jobId = job.job_id;
  mount.innerHTML = `<div class="skeleton" style="width:100%;height:100%;min-height:240px"></div>`;
  try {
    const blob = await api.fetchBlob(api.downloadUrl(job.job_id));
    const url = URL.createObjectURL(blob);
    objectUrls.push(url);
    mount.innerHTML = "";
    playerHandle = mountPlayer(mount, {
      jobId: job.job_id,
      srcObjectUrl: url,
      onRegenerate: () => {
        const draft = loadDraft() || {};
        ctx.submitGeneration(
          {
            ...draft,
            prompt: job.prompt,
            engine: job.engine_requested || "auto",
            aspect_ratio: job.aspect_ratio,
            duration: job.duration,
            idempotency_key: `vera-regen-${Date.now()}`,
          },
          { studio: true }
        );
      },
      onDuplicate: () => {
        saveDraft({
          prompt: job.prompt,
          engine: job.engine_requested || job.engine || "auto",
          aspect_ratio: job.aspect_ratio,
          duration: job.duration,
          quality: job.quality,
        });
        ctx.navigate("create");
        ctx.toast({ title: "Settings copied to Create", tone: "success" });
      },
    });
  } catch (err) {
    mount.innerHTML = `<div class="empty">Preview unavailable · ${escapeHtml(err.message)}</div>`;
  }
}

export function renderProjects(root, ctx) {
  const q = (state._projectQuery || "").toLowerCase();
  const filter = state._projectFilter || "all";
  const sort = state._projectSort || "newest";
  let jobs = [...(state.jobs || [])];
  if (filter === "ready") jobs = jobs.filter((j) => j.ready);
  if (filter === "failed") jobs = jobs.filter((j) => j.status === "FAILED");
  if (filter === "active") jobs = jobs.filter((j) => ACTIVE.has(j.status));
  if (q) jobs = jobs.filter((j) => (j.prompt || "").toLowerCase().includes(q) || (j.job_id || "").includes(q));
  jobs.sort((a, b) => {
    const da = new Date(a.created_at || 0).getTime();
    const db = new Date(b.created_at || 0).getTime();
    return sort === "oldest" ? da - db : db - da;
  });

  root.innerHTML = `
    <div class="toolbar">
      <input type="search" placeholder="Search projects" value="${escapeHtml(
        state._projectQuery || ""
      )}" id="projectSearch" />
      <select id="projectFilter">
        <option value="all" ${filter === "all" ? "selected" : ""}>All</option>
        <option value="ready" ${filter === "ready" ? "selected" : ""}>Ready</option>
        <option value="active" ${filter === "active" ? "selected" : ""}>Active</option>
        <option value="failed" ${filter === "failed" ? "selected" : ""}>Failed</option>
      </select>
      <select id="projectSort">
        <option value="newest" ${sort === "newest" ? "selected" : ""}>Newest</option>
        <option value="oldest" ${sort === "oldest" ? "selected" : ""}>Oldest</option>
      </select>
      <div class="view-toggle" role="group" aria-label="Layout">
        <button type="button" class="${state.galleryView === "grid" ? "active" : ""}" data-view="grid">Grid</button>
        <button type="button" class="${state.galleryView === "list" ? "active" : ""}" data-view="list">List</button>
      </div>
    </div>
    <div class="gallery ${state.galleryView}" id="projectGallery">
      ${
        jobs.length
          ? jobs
              .map(
                (j, i) => `
        <button type="button" class="project-tile" data-open-job="${j.job_id}" style="animation-delay:${
                  i * 40
                }ms">
          <div class="project-thumb">
            ${
              j.has_thumbnail
                ? `<img data-thumb="${j.job_id}" alt="" />`
                : `<span>${escapeHtml(j.aspect_ratio || "—")}</span>`
            }
          </div>
          <div class="project-body">
            <h3>${escapeHtml((j.prompt || "Untitled").slice(0, 72))}</h3>
            <div class="meta-row">
              <span class="status-tag ${escapeHtml(j.status)}">${escapeHtml(j.status)}</span>
              <span>${escapeHtml(j.engine || "—")}</span>
              <span>${escapeHtml(String(j.duration ?? ""))}s</span>
              <span>${escapeHtml(formatDate(j.created_at))}</span>
            </div>
          </div>
        </button>`
              )
              .join("")
          : `<div class="empty">No projects match these filters</div>`
      }
    </div>
  `;

  root.querySelector("#projectSearch")?.addEventListener("input", (e) => {
    state._projectQuery = e.target.value;
    renderProjects(root, ctx);
  });
  root.querySelector("#projectFilter")?.addEventListener("change", (e) => {
    state._projectFilter = e.target.value;
    renderProjects(root, ctx);
  });
  root.querySelector("#projectSort")?.addEventListener("change", (e) => {
    state._projectSort = e.target.value;
    renderProjects(root, ctx);
  });
  root.querySelectorAll("[data-view]").forEach((b) =>
    b.addEventListener("click", () => {
      setGalleryView(b.dataset.view);
      renderProjects(root, ctx);
    })
  );
  hydrateThumbs(root);
  staggerIn(root.querySelectorAll(".project-tile"));
}

async function hydrateThumbs(root) {
  for (const img of root.querySelectorAll("img[data-thumb]")) {
    const id = img.dataset.thumb;
    try {
      const blob = await api.fetchBlob(api.thumbnailUrl(id));
      const url = URL.createObjectURL(blob);
      objectUrls.push(url);
      img.src = url;
    } catch {
      img.replaceWith(Object.assign(document.createElement("span"), { textContent: "No thumb" }));
    }
  }
}

export function renderAssets(root, ctx) {
  const ready = (state.jobs || []).filter((j) => j.ready);
  root.innerHTML = `
    <p class="lede">Generated videos and thumbnails from completed jobs. Uploaded reference images are reused from Create.</p>
    <div class="gallery grid" style="margin-top:1rem">
      ${
        ready.length
          ? ready
              .map(
                (j) => `
        <div class="project-tile" style="cursor:default">
          <div class="project-thumb">${
            j.has_thumbnail ? `<img data-thumb="${j.job_id}" alt="" />` : `<span>MP4</span>`
          }</div>
          <div class="project-body">
            <h3>${escapeHtml(j.asset_id || abbreviateId(j.job_id))}</h3>
            <div class="meta-row">
              <span>${escapeHtml(j.engine || "")}</span>
              <span>${escapeHtml(String(j.output_duration ?? j.duration ?? ""))}s</span>
            </div>
            <div style="display:flex;gap:0.4rem;margin-top:0.65rem;flex-wrap:wrap">
              <button type="button" class="btn btn-sm" data-open-job="${j.job_id}">Open</button>
              <button type="button" class="btn btn-sm btn-ghost" data-dl="${j.job_id}">Download</button>
            </div>
          </div>
        </div>`
              )
              .join("")
          : `<div class="empty">No ready assets yet</div>`
      }
    </div>
  `;
  root.querySelectorAll("[data-dl]").forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        await api.downloadJob(b.dataset.dl);
      } catch (err) {
        ctx.toast({ title: "Download failed", body: err.message, tone: "error" });
      }
    })
  );
  hydrateThumbs(root);
}

export function renderQueue(root, ctx) {
  const jobs = state.jobs || [];
  const groups = {
    ACTIVE: jobs.filter((j) => ACTIVE.has(j.status)),
    QUEUED: jobs.filter((j) => j.status === "QUEUED"),
    COMPLETED: jobs.filter((j) => j.status === "COMPLETED").slice(0, 20),
    FAILED: jobs.filter((j) => j.status === "FAILED").slice(0, 20),
  };
  root.innerHTML = Object.entries(groups)
    .map(
      ([label, list]) => `
    <div class="section-head"><h2>${label}</h2><span class="muted">${list.length}</span></div>
    ${
      list.length
        ? list
            .map((j) => {
              const started = j.started_at ? new Date(j.started_at).getTime() : null;
              const elapsed = started ? formatElapsed(Date.now() - started) : "—";
              return `
              <div class="gen-card" style="margin-bottom:0.55rem">
                <div class="gen-card-top">
                  <div>
                    <strong>${escapeHtml(abbreviateId(j.job_id))}</strong>
                    <div class="gen-meta">${escapeHtml((j.prompt || "").slice(0, 80))}</div>
                    <div class="gen-meta">${escapeHtml(j.engine || "—")} · elapsed ${escapeHtml(
                      elapsed
                    )} · ${escapeHtml(formatDate(j.created_at))}</div>
                    ${stageTrack(j.status)}
                    ${
                      j.status === "FAILED"
                        ? `<div class="gen-meta" style="color:var(--bad)">${escapeHtml(
                            humanFailure(j).summary
                          )}</div>`
                        : ""
                    }
                  </div>
                  <div style="display:flex;flex-direction:column;gap:0.35rem;align-items:end">
                    <span class="status-tag ${escapeHtml(j.status)}">${escapeHtml(j.status)}</span>
                    ${
                      ACTIVE.has(j.status)
                        ? `<button type="button" class="btn btn-sm btn-danger" data-cancel="${j.job_id}">Cancel</button>`
                        : `<button type="button" class="btn btn-sm" data-open-job="${j.job_id}">Open</button>`
                    }
                  </div>
                </div>
              </div>`;
            })
            .join("")
        : `<div class="empty">Nothing here</div>`
    }`
    )
    .join("");

  root.querySelectorAll("[data-cancel]").forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        await api.cancelJob(b.dataset.cancel);
        forgetActiveJob(b.dataset.cancel);
        ctx.refreshJobs();
      } catch (err) {
        ctx.toast({ title: "Cancel failed", body: err.message, tone: "error" });
      }
    })
  );
}

export function renderModels(root) {
  const providers = state.providers || [];
  const readiness = state.readiness || {};
  root.innerHTML = `
    <p class="lede">Availability comes from backend readiness — never from worker HTTP 200 alone.</p>
    <div class="engine-grid" style="margin-top:1rem">
      ${
        providers.length
          ? providers
              .map((p) => {
                const meta = ENGINE_META[p.name] || { label: p.name, blurb: "" };
                const ready = isProviderReady(p) && (readiness.ready_engines || []).includes(p.name);
                return `
            <article class="engine-tile">
              <div class="gen-card-top">
                <h3>${escapeHtml(meta.label || p.name)}</h3>
                <span class="status-tag ${ready ? "ok" : "bad"}">${ready ? "READY" : escapeHtml(providerStatusLabel(p.status))}</span>
              </div>
              <p class="hint">${escapeHtml(meta.blurb || "")}</p>
              <div class="cap-list">
                ${(p.tasks || [])
                  .map((t) => `<span class="chip">${escapeHtml(t)}</span>`)
                  .join("")}
                ${meta.requiresCuda ? `<span class="chip">CUDA required</span>` : `<span class="chip">CPU capable</span>`}
                ${p.installed ? `<span class="chip active">Installed</span>` : `<span class="chip">Not installed</span>`}
              </div>
              <div class="gen-meta" style="margin-top:0.75rem">min VRAM · ${escapeHtml(
                String(p.min_vram_gb ?? "—")
              )} GB</div>
            </article>`;
              })
              .join("")
          : `<div class="empty">No engine catalog loaded</div>`
      }
    </div>
  `;
  staggerIn(root.querySelectorAll(".engine-tile"));
}

export function renderAnalytics(root) {
  const m = state.metrics;
  if (!m) {
    root.innerHTML = `<div class="empty">No metrics yet — generate something first</div>`;
    return;
  }
  const totals = m.totals || {};
  const usage = m.engine_usage || {};
  const max = Math.max(1, ...Object.values(usage).map(Number), 1);
  root.innerHTML = `
    <div class="stat-grid">
      <div class="stat"><div class="label">Jobs</div><div class="value">${totals.jobs ?? 0}</div></div>
      <div class="stat"><div class="label">Completed</div><div class="value">${totals.completed ?? 0}</div></div>
      <div class="stat"><div class="label">Failed</div><div class="value">${totals.failed ?? 0}</div></div>
      <div class="stat"><div class="label">Ready assets</div><div class="value">${totals.ready ?? 0}</div></div>
      <div class="stat"><div class="label">Success rate</div><div class="value">${
        m.render_success_rate == null ? "—" : `${Math.round(m.render_success_rate * 100)}%`
      }</div></div>
      <div class="stat"><div class="label">Avg render</div><div class="value" style="font-size:1.5rem">${
        m.avg_gpu_render_duration_sec == null
          ? "—"
          : `${Number(m.avg_gpu_render_duration_sec).toFixed(1)}s`
      }</div></div>
    </div>
    <div class="section-head"><h2>Engine usage</h2></div>
    ${
      Object.keys(usage).length
        ? `<div class="bar-chart">${Object.entries(usage)
            .map(
              ([k, v]) => `
          <div class="bar-row">
            <span>${escapeHtml(k)}</span>
            <div class="bar-track"><div class="bar-fill" style="width:${(Number(v) / max) * 100}%"></div></div>
            <span>${escapeHtml(String(v))}</span>
          </div>`
            )
            .join("")}</div>`
        : `<div class="empty">No engine usage recorded yet</div>`
    }
  `;
}

export function renderSystem(root) {
  const h = state.health || {};
  const r = state.readiness || {};
  const details = h.details || {};
  const worker = h.worker || {};
  const devices = worker.devices || [];
  root.innerHTML = `
    <div class="stat-grid">
      <div class="stat"><div class="label">Gateway</div><div class="value" style="font-size:1.4rem;color:var(--ok)">ONLINE</div></div>
      <div class="stat"><div class="label">Worker</div><div class="value" style="font-size:1.4rem;color:${
        r.worker_available || details.worker_configured ? "var(--ok)" : "var(--bad)"
      }">${r.worker_available ? "CONNECTED" : details.worker_configured ? "CONFIGURED" : "DISCONNECTED"}</div></div>
      <div class="stat"><div class="label">CUDA</div><div class="value" style="font-size:1.4rem;color:${
        r.cuda_available ? "var(--ok)" : "var(--warn)"
      }">${r.cuda_available ? "AVAILABLE" : "UNAVAILABLE"}</div></div>
      <div class="stat"><div class="label">Generation</div><div class="value" style="font-size:1.4rem;color:${
        r.generation_available ? "var(--ok)" : "var(--bad)"
      }">${r.generation_available ? "READY" : "BLOCKED"}</div></div>
    </div>
    <div class="section-head"><h2>GPU</h2></div>
    <div class="rail">
      ${
        devices.length
          ? devices
              .map(
                (d) =>
                  `<div class="gen-meta">${escapeHtml(d.name || "GPU")} · ${escapeHtml(
                    String(d.total_mb ?? "—")
                  )} MB total · ${escapeHtml(String(d.free_mb ?? "—"))} MB free</div>`
              )
              .join("")
          : `<div class="empty">No GPU details exposed</div>`
      }
      <div class="gen-meta" style="margin-top:0.55rem">VRAM total · ${escapeHtml(
        String(r.vram_total_mb ?? details.vram_total_mb ?? "—")
      )} · free · ${escapeHtml(String(r.vram_free_mb ?? details.vram_free_mb ?? "—"))}</div>
      <div class="gen-meta">Queue depth · ${escapeHtml(String(h.queue_depth ?? r.queue_depth ?? 0))}</div>
      <div class="gen-meta">Ready engines · ${escapeHtml(
        (r.ready_engines || h.ready_engines || []).join(", ") || "none"
      )}</div>
      <div class="gen-meta">CPU assembly · ${
        r.cpu_assembly_ready ? "ready" : "unavailable"
      } · LTX · ${r.ltx_ready ? "ready" : "not ready"}</div>
    </div>
  `;
}

export function renderSettings(root, ctx) {
  const s = loadSettings();
  root.innerHTML = `
    <form id="settingsForm" class="composer" style="max-width:560px">
      <label class="field"><span>Default engine</span>
        <select name="defaultEngine">${engineOptions(s.defaultEngine, {
          readiness: state.readiness,
          providers: state.providers,
        })}</select>
      </label>
      <label class="field"><span>Default aspect</span>
        <select name="defaultAspect">
          ${["9:16", "16:9", "1:1", "4:5"]
            .map((a) => `<option ${s.defaultAspect === a ? "selected" : ""}>${a}</option>`)
            .join("")}
        </select>
      </label>
      <label class="field"><span>Default duration</span>
        <input name="defaultDuration" type="number" min="1" max="120" value="${s.defaultDuration}" />
      </label>
      <label class="field"><span>Default quality</span>
        <select name="defaultQuality">
          ${["draft", "standard", "high", "max"]
            .map((q) => `<option ${s.defaultQuality === q ? "selected" : ""}>${q}</option>`)
            .join("")}
        </select>
      </label>
      <button type="submit" class="btn btn-primary">Save preferences</button>
    </form>
  `;
  root.querySelector("#settingsForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    saveSettings({
      defaultEngine: fd.get("defaultEngine"),
      defaultAspect: fd.get("defaultAspect"),
      defaultDuration: Number(fd.get("defaultDuration") || 5),
      defaultQuality: fd.get("defaultQuality"),
      allowShort: true,
    });
    ctx.toast({ title: "Preferences saved", tone: "success" });
  });
}

export function renderApi(root) {
  root.innerHTML = `
    <div class="composer" style="max-width:640px">
      <h2 class="display" style="font-size:2rem;margin:0 0 0.5rem">API</h2>
      <p class="lede">Authenticated REST control plane. All studio actions use the same endpoints.</p>
      <div class="details-box" style="margin-top:1rem">
        <div><code>POST /v1/videos</code> — create generation</div>
        <div><code>GET /v1/videos/{id}</code> — poll status</div>
        <div><code>GET /v1/videos/{id}/download</code> — MP4 when ready</div>
        <div><code>POST /v1/uploads/image</code> — reference image</div>
        <div><code>GET /v1/readiness</code> — engine readiness</div>
        <div><code>GET /v1/providers</code> — engine catalog</div>
        <div><code>GET /v1/metrics</code> — real analytics</div>
      </div>
      <p class="hint" style="margin-top:0.85rem">Send header <code>X-API-Key</code>. Never commit keys.</p>
    </div>
  `;
}

export function renderAccount(root, ctx) {
  root.innerHTML = `
    <div class="composer" style="max-width:480px">
      <h2 class="display" style="font-size:2rem;margin:0 0 0.5rem">Account</h2>
      <p class="lede">API key is stored only in this browser’s local storage.</p>
      <label class="field"><span>API key</span>
        <input id="acctKey" type="password" value="${escapeHtml(api.getApiKey())}" />
      </label>
      <div style="display:flex;gap:0.5rem;flex-wrap:wrap">
        <button type="button" class="btn btn-primary" id="saveKey">Update key</button>
        <button type="button" class="btn btn-ghost" id="signOut">Sign out</button>
      </div>
    </div>
  `;
  root.querySelector("#saveKey")?.addEventListener("click", () => {
    api.setApiKey(root.querySelector("#acctKey").value.trim());
    ctx.toast({ title: "API key updated", tone: "success" });
    ctx.bootstrap();
  });
  root.querySelector("#signOut")?.addEventListener("click", () => {
    api.setApiKey("");
    ctx.signOut();
  });
}

export { revokeUrls, ACTIVE, rememberActiveJob, forgetActiveJob };

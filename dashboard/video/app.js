(() => {
  const $ = (s) => document.querySelector(s);
  const apiKeyEl = $("#apiKey");
  apiKeyEl.value = localStorage.getItem("vg_api_key") || "";
  apiKeyEl.addEventListener("change", () => localStorage.setItem("vg_api_key", apiKeyEl.value));

  document.querySelectorAll("nav button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("nav button").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      $(`#${btn.dataset.tab}`).classList.add("active");
      if (btn.dataset.tab === "jobs") loadJobs();
      if (btn.dataset.tab === "providers") loadProviders();
      if (btn.dataset.tab === "models") loadModels();
      if (btn.dataset.tab === "metrics") loadMetrics();
      if (btn.dataset.tab === "outputs") loadOutputs();
      if (btn.dataset.tab === "health") loadHealth();
    });
  });

  function headers() {
    const h = { "Content-Type": "application/json" };
    if (apiKeyEl.value) h["X-API-Key"] = apiKeyEl.value;
    return h;
  }

  async function api(path, opts = {}) {
    const r = await fetch(path, { ...opts, headers: { ...headers(), ...(opts.headers || {}) } });
    const text = await r.text();
    let data;
    try { data = JSON.parse(text); } catch { data = { raw: text }; }
    if (!r.ok) throw new Error(data.detail || data.error || r.statusText);
    return data;
  }

  async function downloadWithAuth(jobId) {
    const r = await fetch(`/v1/videos/${jobId}/download`, { headers: headers() });
    if (!r.ok) {
      const text = await r.text();
      throw new Error(text || r.statusText);
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${jobId}.mp4`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function loadHealth() {
    try {
      const h = await api("/health");
      $("#healthOut").textContent = JSON.stringify(h, null, 2);
    } catch (e) {
      $("#healthOut").textContent = String(e);
    }
  }

  async function loadProviders() {
    try {
      const rows = await api("/v1/providers");
      $("#providersOut").innerHTML = rows.map((p) => `
        <div class="card">
          <h3>${p.name}</h3>
          <span class="badge ${p.status}">${p.status.replaceAll("_", " ")}</span>
          <p>installed: ${p.installed} · ready: ${p.ready}</p>
          <p>${(p.tasks || []).join(", ")}</p>
        </div>`).join("");
    } catch (e) {
      $("#providersOut").textContent = String(e);
    }
  }

  async function loadJobs() {
    try {
      const data = await api("/v1/jobs?limit=50");
      $("#jobsOut").innerHTML = (data.jobs || []).map((j) => `
        <div class="row-item">
          <div>
            <strong>${j.job_id}</strong>
            <div>${j.status} · ${j.engine || "—"} · ready=${j.ready}</div>
            <div style="color:#5a6570;font-size:0.85rem">${(j.prompt || "").slice(0, 120)}</div>
            ${j.failure_reason ? `<div style="color:#b33;font-size:0.85rem">${j.failure_category || ""}: ${j.failure_reason}</div>` : ""}
          </div>
          <div>${j.duration || ""}s</div>
        </div>`).join("") || "<p>No jobs yet.</p>";
    } catch (e) {
      $("#jobsOut").textContent = String(e);
    }
  }

  async function loadModels() {
    try {
      const h = await api("/health");
      $("#modelsOut").textContent = JSON.stringify({
        installed_engines: h.installed_engines,
        ready_engines: h.ready_engines,
        model_loading: h.model_loading,
        worker: h.worker,
        details: h.details,
      }, null, 2);
    } catch (e) {
      $("#modelsOut").textContent = String(e);
    }
  }

  async function loadMetrics() {
    try {
      const m = await api("/v1/metrics");
      $("#metricsOut").textContent = JSON.stringify(m, null, 2);
    } catch (e) {
      $("#metricsOut").textContent = String(e);
    }
  }

  async function loadOutputs() {
    try {
      const data = await api("/v1/jobs?limit=50");
      const ready = (data.jobs || []).filter((j) => j.ready);
      $("#outputsOut").innerHTML = ready.map((j) => `
        <div class="row-item">
          <div>
            <strong>${j.asset_id || j.job_id}</strong>
            <div>${j.resolution} · ${j.output_duration}s · QC ${j.qc_status}</div>
            <div style="font-size:0.85rem">${j.output_location || ""}</div>
          </div>
          <button type="button" data-dl="${j.job_id}">Download</button>
        </div>`).join("") || "<p>No READY assets.</p>";
      $("#outputsOut").querySelectorAll("button[data-dl]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          try {
            await downloadWithAuth(btn.dataset.dl);
          } catch (e) {
            alert(String(e));
          }
        });
      });
    } catch (e) {
      $("#outputsOut").textContent = String(e);
    }
  }

  let pollTimer = null;
  let submitting = false;
  let activeJobId = localStorage.getItem("vg_active_job_id") || null;

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function renderJob(job) {
    const live = $("#liveJob");
    const canPlay = job.ready === true && job.status === "COMPLETED";
    live.innerHTML = `
      <pre class="code">${JSON.stringify({
        job_id: job.job_id,
        status: job.status,
        engine: job.engine,
        ready: job.ready,
        failure_category: job.failure_category,
        failure_reason: job.failure_reason,
        output_location: job.output_location,
        output_duration: job.output_duration,
      }, null, 2)}</pre>
      ${canPlay ? `<p><button type="button" id="dlReady">Download / Play MP4</button></p>
        <video id="readyVideo" controls style="max-width:320px;width:100%"></video>` : ""}
    `;
    if (canPlay) {
      $("#dlReady")?.addEventListener("click", async () => {
        try {
          await downloadWithAuth(job.job_id);
          const r = await fetch(`/v1/videos/${job.job_id}/download`, { headers: headers() });
          const blob = await r.blob();
          const url = URL.createObjectURL(blob);
          const vid = $("#readyVideo");
          if (vid) vid.src = url;
        } catch (e) {
          alert(String(e));
        }
      });
    }
  }

  async function pollJob(jobId) {
    stopPolling();
    activeJobId = jobId;
    localStorage.setItem("vg_active_job_id", jobId);
    const tick = async () => {
      try {
        const job = await api(`/v1/videos/${jobId}`);
        renderJob(job);
        if (["COMPLETED", "FAILED", "CANCELLED"].includes(job.status)) {
          stopPolling();
          if (job.status !== "COMPLETED") {
            localStorage.removeItem("vg_active_job_id");
          }
        }
      } catch (e) {
        $("#liveJob").textContent = String(e);
        stopPolling();
      }
    };
    await tick();
    pollTimer = setInterval(tick, 1500);
  }

  $("#genForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (submitting) return;
    submitting = true;
    const submitBtn = ev.target.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;
    const fd = new FormData(ev.target);
    // Stable idempotency key per form submit — prevents duplicate renders on retry.
    const idem = `dash-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    const body = {
      prompt: fd.get("prompt"),
      duration: Number(fd.get("duration") || 30),
      aspect_ratio: fd.get("aspect_ratio"),
      quality: fd.get("quality"),
      engine: fd.get("engine"),
      negative_prompt: fd.get("negative_prompt") || "",
      audio: fd.get("audio") === "on",
      captions: fd.get("captions") === "on",
      allow_short: fd.get("allow_short") === "on",
      idempotency_key: idem,
    };
    const seed = fd.get("seed");
    if (seed !== "" && seed != null) body.seed = Number(seed);
    if (fd.get("input_image")) body.input_image = fd.get("input_image");
    if (fd.get("input_video")) body.input_video = fd.get("input_video");
    const live = $("#liveJob");
    live.textContent = "Submitting…";
    try {
      const created = await api("/v1/videos", { method: "POST", body: JSON.stringify(body) });
      live.textContent = `Queued ${created.job_id}`;
      await pollJob(created.job_id);
    } catch (e) {
      live.textContent = String(e);
    } finally {
      submitting = false;
      if (submitBtn) submitBtn.disabled = false;
    }
  });

  $("#refreshJobs")?.addEventListener("click", loadJobs);

  // Resume polling a previously submitted job (never re-submit).
  if (activeJobId) {
    pollJob(activeJobId);
  } else {
    loadHealth();
  }
})();

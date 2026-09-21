/** Engine capability map — UI must follow backend truth. */

export const STAGES = [
  "QUEUED",
  "PLANNING",
  "ROUTING",
  "DOWNLOADING_MODEL",
  "RENDERING",
  "STITCHING",
  "AUDIO",
  "QC",
  "COMPLETED",
];

export const ENGINE_META = {
  auto: {
    label: "Auto",
    blurb: "Routes to the best ready engine for this request.",
    t2v: true,
    i2v: true,
    negative: true,
    seed: true,
    motion: false,
    camera: false,
    requiresCuda: false,
  },
  fast: {
    label: "Fast",
    blurb: "Prefers lower-latency ready engines.",
    t2v: true,
    i2v: true,
    negative: true,
    seed: true,
    requiresCuda: false,
  },
  quality: {
    label: "Quality",
    blurb: "Prefers higher-quality ready engines when available.",
    t2v: true,
    i2v: true,
    negative: true,
    seed: true,
    requiresCuda: false,
  },
  low_vram: {
    label: "Low VRAM",
    blurb: "Biases toward low-resource ready engines.",
    t2v: true,
    i2v: true,
    negative: true,
    seed: true,
    requiresCuda: false,
  },
  wan: {
    label: "Wan 2.2",
    blurb: "Open diffusion video model. Requires CUDA + installed weights.",
    t2v: true,
    i2v: true,
    negative: true,
    seed: true,
    requiresCuda: true,
    diffusion: true,
  },
  ltx: {
    label: "LTX Video",
    blurb: "Open diffusion video model. Requires CUDA + installed weights.",
    t2v: true,
    i2v: true,
    negative: true,
    seed: true,
    requiresCuda: true,
    diffusion: true,
  },
  framepack: {
    label: "FramePack",
    blurb: "Image-to-video / long-form path. Requires CUDA + installed weights.",
    t2v: false,
    i2v: true,
    negative: true,
    seed: true,
    requiresCuda: true,
    diffusion: true,
  },
  cpu_assembly: {
    label: "CPU Assembly",
    blurb:
      "Deterministic FFmpeg assembly from stills with pan/zoom — real H.264 MP4, not diffusion.",
    t2v: true,
    i2v: true,
    negative: false,
    seed: false,
    requiresCuda: false,
    diffusion: false,
    assembly: true,
  },
};

export function capsFor(engine) {
  return ENGINE_META[engine] || ENGINE_META.auto;
}

export function providerStatusLabel(status) {
  return String(status || "UNKNOWN").replaceAll("_", " ");
}

export function isProviderReady(p) {
  return p?.status === "AVAILABLE" && p?.ready === true;
}

export function enginesSupporting(mode, providers) {
  const list = providers || [];
  if (mode === "i2v") {
    return list.filter((p) => (p.tasks || []).includes("image-to-video"));
  }
  return list.filter((p) => (p.tasks || []).includes("text-to-video"));
}

export function anyReadyFor(mode, readiness, providers) {
  const ready = new Set(readiness?.ready_engines || []);
  if (!ready.size) return false;
  if (mode === "i2v") {
    return [...ready].some((name) => {
      const p = providers.find((x) => x.name === name);
      const meta = capsFor(name);
      return meta.i2v && (p ? (p.tasks || []).includes("image-to-video") : meta.i2v);
    });
  }
  return [...ready].some((name) => capsFor(name).t2v);
}

export function humanFailure(job) {
  const cat = job.failure_category || "generation_failed";
  const map = {
    worker_bad_gateway: "The GPU worker could not complete this render (upstream 502).",
    worker_timeout: "The worker timed out before finishing.",
    cuda_unavailable: "CUDA is unavailable for the selected engine.",
    engine_not_ready: "The selected engine is not ready.",
    model_not_loaded: "Required model weights are not loaded.",
    generation_unavailable: "No compatible engine is ready right now.",
    qc_failed: "Output failed quality control.",
    stitching_failed: "Scene stitching failed.",
    insufficient_vram: "Not enough GPU memory.",
    all_engines_failed: "All candidate engines failed.",
  };
  return {
    title: "Generation failed",
    summary: map[cat] || "Generation could not be completed.",
    category: cat,
    detail: job.failure_reason || "",
  };
}

export function stageIndex(status) {
  const i = STAGES.indexOf(status);
  if (status === "FAILED" || status === "CANCELLED") return -1;
  return i;
}

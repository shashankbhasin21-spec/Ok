import { el, formatClock, downloadBlobFile } from "../ui.js";

/**
 * Premium custom video player. Playback disabled until a valid blob/src exists.
 */
export function createVideoPlayer({
  aspectRatio = "16:9",
  onRegenerate,
  onDuplicate,
  onVariation,
  onDownload,
} = {}) {
  const video = el("video", {
    playsinline: true,
    preload: "metadata",
    "aria-label": "Generated video preview",
  });
  video.controls = false;

  const playBtn = el("button", {
    type: "button",
    className: "ls-icon-btn",
    "aria-label": "Play",
    disabled: true,
    text: "▶",
  });
  const muteBtn = el("button", {
    type: "button",
    className: "ls-icon-btn",
    "aria-label": "Mute",
    disabled: true,
    text: "🔊",
  });
  const fullBtn = el("button", {
    type: "button",
    className: "ls-icon-btn",
    "aria-label": "Fullscreen",
    disabled: true,
    text: "⛶",
  });
  const scrub = el("input", {
    type: "range",
    className: "ls-player__scrub",
    min: "0",
    max: "0",
    value: "0",
    step: "0.05",
    disabled: true,
    "aria-label": "Seek",
  });
  const time = el("div", { className: "ls-player__time", text: "0:00 / 0:00" });
  const volume = el("input", {
    type: "range",
    min: "0",
    max: "1",
    step: "0.05",
    value: "1",
    disabled: true,
    "aria-label": "Volume",
    style: "width:72px",
  });

  const stage = el("div", { className: "ls-player__stage", dataset: { ratio: aspectRatio } }, [
    video,
  ]);

  const actions = el("div", { className: "ls-player__actions" });

  const root = el("div", { className: "ls-player" }, [
    stage,
    el("div", { className: "ls-player__controls" }, [
      playBtn,
      el("div", { style: "display:flex;flex-direction:column;gap:0.35rem;min-width:0" }, [scrub, time]),
      el("div", { style: "display:flex;align-items:center;gap:0.25rem" }, [muteBtn, volume, fullBtn]),
    ]),
    actions,
  ]);

  let objectUrl = null;
  let ready = false;
  let jobId = null;

  function setEnabled(on) {
    ready = on;
    playBtn.disabled = !on;
    muteBtn.disabled = !on;
    fullBtn.disabled = !on;
    scrub.disabled = !on;
    volume.disabled = !on;
  }

  function syncTime() {
    time.textContent = `${formatClock(video.currentTime)} / ${formatClock(video.duration || 0)}`;
    if (!Number.isNaN(video.duration) && video.duration) {
      scrub.max = String(video.duration);
      scrub.value = String(video.currentTime || 0);
    }
  }

  function renderActions() {
    actions.replaceChildren();
    if (!ready) return;
    const mk = (label, fn, cls = "ls-btn ls-btn--sm ls-btn--ghost") =>
      el("button", { type: "button", className: cls, text: label, onClick: fn });
    if (onDownload) actions.append(mk("Download", () => onDownload(jobId), "ls-btn ls-btn--sm ls-btn--primary"));
    if (onRegenerate) actions.append(mk("Regenerate", () => onRegenerate(jobId)));
    if (onDuplicate) actions.append(mk("Duplicate settings", () => onDuplicate(jobId)));
    if (onVariation) actions.append(mk("Create variation", () => onVariation(jobId)));
  }

  playBtn.addEventListener("click", () => {
    if (!ready) return;
    if (video.paused) video.play();
    else video.pause();
  });
  video.addEventListener("play", () => { playBtn.textContent = "❚❚"; playBtn.setAttribute("aria-label", "Pause"); });
  video.addEventListener("pause", () => { playBtn.textContent = "▶"; playBtn.setAttribute("aria-label", "Play"); });
  video.addEventListener("timeupdate", syncTime);
  video.addEventListener("loadedmetadata", syncTime);
  scrub.addEventListener("input", () => {
    if (!ready) return;
    video.currentTime = Number(scrub.value);
  });
  muteBtn.addEventListener("click", () => {
    video.muted = !video.muted;
    muteBtn.textContent = video.muted ? "🔇" : "🔊";
  });
  volume.addEventListener("input", () => {
    video.volume = Number(volume.value);
    video.muted = video.volume === 0;
  });
  fullBtn.addEventListener("click", async () => {
    if (!document.fullscreenElement) await stage.requestFullscreen?.();
    else await document.exitFullscreen?.();
  });

  async function loadBlob(blob, { id = null, ratio = aspectRatio } = {}) {
    clear();
    if (!blob) return;
    jobId = id;
    objectUrl = URL.createObjectURL(blob);
    video.src = objectUrl;
    stage.dataset.ratio = ratio || aspectRatio;
    setEnabled(true);
    renderActions();
    await video.play().catch(() => {});
    video.pause();
  }

  function clear() {
    video.pause();
    video.removeAttribute("src");
    video.load();
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
    jobId = null;
    setEnabled(false);
    syncTime();
    actions.replaceChildren();
  }

  function destroy() {
    clear();
  }

  setEnabled(false);

  return {
    root,
    loadBlob,
    clear,
    destroy,
    downloadCurrent: async (blob, name) => downloadBlobFile(blob, name),
  };
}

export default createVideoPlayer;

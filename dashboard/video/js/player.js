/** Premium custom video player — only mounts when a valid blob/src exists. */

import { downloadJob } from "./api.js";
import { escapeHtml } from "./motion.js";

export function mountPlayer(container, { jobId, srcObjectUrl, onRegenerate, onDuplicate }) {
  container.innerHTML = `
    <div class="player" tabindex="0">
      <video playsinline preload="metadata"></video>
      <div class="player-controls">
        <button type="button" class="icon-btn" data-act="play" aria-label="Play">▶</button>
        <input class="scrubber" type="range" min="0" max="1000" value="0" aria-label="Seek" />
        <div style="display:flex;gap:0.35rem;align-items:center">
          <span class="time muted" style="font-size:0.75rem">0:00</span>
          <button type="button" class="icon-btn" data-act="mute" aria-label="Mute">🔊</button>
          <button type="button" class="icon-btn" data-act="full" aria-label="Fullscreen">⛶</button>
        </div>
      </div>
    </div>
    <div class="player-actions">
      <button type="button" class="btn btn-primary btn-sm" data-act="download">Download</button>
      <button type="button" class="btn btn-sm" data-act="regen">Regenerate</button>
      <button type="button" class="btn btn-sm" data-act="dup">Duplicate settings</button>
    </div>
  `;

  const root = container.querySelector(".player");
  const video = root.querySelector("video");
  const scrubber = root.querySelector(".scrubber");
  const timeEl = root.querySelector(".time");
  video.src = srcObjectUrl;

  const fmt = (t) => {
    if (!Number.isFinite(t)) return "0:00";
    const m = Math.floor(t / 60);
    const s = Math.floor(t % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  };

  const sync = () => {
    if (video.duration) {
      scrubber.value = String(Math.floor((video.currentTime / video.duration) * 1000));
    }
    timeEl.textContent = `${fmt(video.currentTime)} / ${fmt(video.duration || 0)}`;
  };

  video.addEventListener("timeupdate", sync);
  video.addEventListener("loadedmetadata", sync);

  container.addEventListener("click", async (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (!act) return;
    if (act === "play") {
      if (video.paused) {
        await video.play();
        e.target.textContent = "❚❚";
      } else {
        video.pause();
        e.target.textContent = "▶";
      }
    } else if (act === "mute") {
      video.muted = !video.muted;
      e.target.textContent = video.muted ? "🔇" : "🔊";
    } else if (act === "full") {
      if (root.requestFullscreen) root.requestFullscreen();
    } else if (act === "download") {
      await downloadJob(jobId);
    } else if (act === "regen") {
      onRegenerate?.();
    } else if (act === "dup") {
      onDuplicate?.();
    }
  });

  scrubber.addEventListener("input", () => {
    if (!video.duration) return;
    video.currentTime = (Number(scrubber.value) / 1000) * video.duration;
  });

  return {
    destroy() {
      video.pause();
      video.removeAttribute("src");
      video.load();
    },
  };
}

export function jobSummaryHtml(job) {
  return `
    <div class="meta-row">
      <span class="status-tag ${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
      <span>${escapeHtml(job.engine || "—")}</span>
      <span>${escapeHtml(String(job.duration ?? "—"))}s</span>
      <span>${escapeHtml(job.aspect_ratio || "")}</span>
    </div>
  `;
}

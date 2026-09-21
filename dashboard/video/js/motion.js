/** Motion helpers — transform/opacity first, respects reduced motion. */

export const reduceMotion =
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export function animate(el, keyframes, options = {}) {
  if (!el) return null;
  if (reduceMotion) {
    const last = keyframes[keyframes.length - 1] || {};
    Object.assign(el.style, flattenStyles(last));
    return null;
  }
  return el.animate(keyframes, {
    duration: 320,
    easing: "cubic-bezier(0.22, 1, 0.36, 1)",
    fill: "both",
    ...options,
  });
}

function flattenStyles(frame) {
  const out = {};
  if (frame.opacity != null) out.opacity = frame.opacity;
  if (frame.transform != null) out.transform = frame.transform;
  return out;
}

export function staggerIn(nodes, { delay = 45 } = {}) {
  [...nodes].forEach((el, i) => {
    animate(
      el,
      [
        { opacity: 0, transform: "translateY(12px)" },
        { opacity: 1, transform: "translateY(0)" },
      ],
      { delay: i * delay, duration: 420 }
    );
  });
}

export function magnetic(btn) {
  if (!btn || reduceMotion) return () => {};
  const onMove = (e) => {
    const r = btn.getBoundingClientRect();
    const x = (e.clientX - r.left - r.width / 2) / 10;
    const y = (e.clientY - r.top - r.height / 2) / 10;
    btn.style.transform = `translate(${x}px, ${y}px)`;
  };
  const onLeave = () => {
    btn.style.transform = "";
  };
  btn.addEventListener("pointermove", onMove);
  btn.addEventListener("pointerleave", onLeave);
  return () => {
    btn.removeEventListener("pointermove", onMove);
    btn.removeEventListener("pointerleave", onLeave);
  };
}

export function toast(host, { title, body, tone = "info", ms = 4200 }) {
  const el = document.createElement("div");
  el.className = `toast ${tone === "error" ? "error" : tone === "success" ? "success" : ""}`;
  el.innerHTML = `<strong>${escapeHtml(title)}</strong>${
    body ? `<div class="muted" style="margin-top:0.25rem">${escapeHtml(body)}</div>` : ""
  }`;
  host.appendChild(el);
  animate(el, [{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "none" }]);
  setTimeout(() => {
    const a = animate(el, [{ opacity: 1 }, { opacity: 0 }], { duration: 220 });
    if (a) a.onfinish = () => el.remove();
    else el.remove();
  }, ms);
}

export function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function formatElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m > 0 ? `${m}m ${r}s` : `${r}s`;
}

export function abbreviateId(id) {
  if (!id) return "—";
  return id.length > 10 ? `${id.slice(0, 6)}…${id.slice(-4)}` : id;
}

export function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return String(iso);
  }
}

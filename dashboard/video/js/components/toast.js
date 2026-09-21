import { el } from "../ui.js";

export function toast(message, { type = "info", title = null, ttl = 4200 } = {}) {
  const host = document.getElementById("ls-toasts");
  if (!host) return;
  const node = el("div", {
    className: "ls-toast",
    role: "status",
    dataset: { type },
  }, [
    title ? el("div", { className: "ls-toast__title", text: title }) : null,
    el("div", { className: "ls-toast__body", text: message }),
  ]);
  host.append(node);
  const t = setTimeout(() => {
    node.style.opacity = "0";
    node.style.transform = "translateY(8px)";
    setTimeout(() => node.remove(), 220);
  }, ttl);
  node.addEventListener("click", () => {
    clearTimeout(t);
    node.remove();
  });
}

export default toast;

import { el } from "../ui.js";
import { navigate } from "../router.js";

const COMMANDS = [
  { id: "create", label: "New generation", hint: "Open Create Studio", route: "create" },
  { id: "i2v", label: "Image → Video", hint: "Animate a still", route: "image-video" },
  { id: "projects", label: "Open Projects", hint: "Gallery", route: "projects" },
  { id: "queue", label: "Open Queue", hint: "Operational jobs", route: "queue" },
  { id: "system", label: "System Status", hint: "Diagnostics", route: "system" },
  { id: "settings", label: "Settings", hint: "API key & prefs", route: "settings" },
];

export function createCommandPalette() {
  const host = document.getElementById("ls-cmd");
  let open = false;
  let index = 0;
  let filtered = COMMANDS;

  const input = el("input", {
    className: "ls-cmd__input",
    type: "search",
    placeholder: "Type a command…",
    "aria-label": "Command palette",
    autocomplete: "off",
  });
  const list = el("div", { className: "ls-cmd__list", role: "listbox", "aria-label": "Commands" });
  const panel = el("div", { className: "ls-cmd__panel", role: "dialog", "aria-modal": "true", "aria-label": "Command palette" }, [
    input,
    list,
  ]);

  function renderList() {
    list.replaceChildren();
    filtered.forEach((cmd, i) => {
      const item = el("button", {
        type: "button",
        className: "ls-cmd__item",
        role: "option",
        "aria-selected": i === index ? "true" : "false",
        onClick: () => run(cmd),
      }, [
        el("span", { text: cmd.label }),
        el("span", { className: "ls-field__hint", text: cmd.hint }),
      ]);
      list.append(item);
    });
  }

  function run(cmd) {
    close();
    navigate(cmd.route);
  }

  function filter(q) {
    const s = q.trim().toLowerCase();
    filtered = !s
      ? COMMANDS
      : COMMANDS.filter((c) => `${c.label} ${c.hint}`.toLowerCase().includes(s));
    index = 0;
    renderList();
  }

  function show() {
    open = true;
    host.hidden = false;
    host.replaceChildren(panel);
    filter("");
    input.value = "";
    requestAnimationFrame(() => input.focus());
  }

  function close() {
    open = false;
    host.hidden = true;
    host.replaceChildren();
  }

  input.addEventListener("input", () => filter(input.value));
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      index = Math.min(filtered.length - 1, index + 1);
      renderList();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      index = Math.max(0, index - 1);
      renderList();
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (filtered[index]) run(filtered[index]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      close();
    }
  });

  host.addEventListener("click", (e) => {
    if (e.target === host) close();
  });

  window.addEventListener("keydown", (e) => {
    const meta = e.metaKey || e.ctrlKey;
    if (meta && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if (open) close();
      else show();
    } else if (e.key === "Escape" && open) close();
  });

  document.getElementById("ls-cmd-open")?.addEventListener("click", show);

  return { show, close, isOpen: () => open };
}

export default createCommandPalette;

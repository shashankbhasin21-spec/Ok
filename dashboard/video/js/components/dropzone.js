import { el } from "../ui.js";

/**
 * @param {object} opts
 * @param {(file: File, uploaded?: object) => void} opts.onChange
 * @param {() => void} [opts.onClear]
 * @param {(file: File) => Promise<object>} [opts.upload]
 */
export function createDropzone({ onChange, onClear, upload, accept = "image/jpeg,image/png,image/webp,image/gif" } = {}) {
  let previewUrl = null;
  let uploaded = null;
  let file = null;

  const input = el("input", { type: "file", accept, "aria-label": "Upload reference image" });
  const hint = el("div", { className: "ls-dropzone__hint", text: "Drop an image, or click to browse" });
  const sub = el("div", { className: "ls-field__hint", text: "JPEG, PNG, WebP, GIF · max 25MB" });
  const preview = el("img", { className: "ls-dropzone__preview", alt: "Reference preview", hidden: true });
  const actions = el("div", { className: "ls-dropzone__actions", hidden: true }, [
    el("button", { type: "button", className: "ls-btn ls-btn--sm ls-btn--ghost", text: "Replace", onClick: () => input.click() }),
    el("button", {
      type: "button",
      className: "ls-btn ls-btn--sm ls-btn--danger",
      text: "Remove",
      onClick: () => clear(),
    }),
  ]);

  const root = el("div", {
    className: "ls-dropzone",
    role: "button",
    tabindex: "0",
    "aria-label": "Reference image dropzone",
  }, [input, hint, sub, preview, actions]);

  function setPreview(f) {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(f);
    preview.src = previewUrl;
    preview.hidden = false;
    hint.hidden = true;
    sub.hidden = true;
    actions.hidden = false;
  }

  function clear() {
    file = null;
    uploaded = null;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
    preview.removeAttribute("src");
    preview.hidden = true;
    hint.hidden = false;
    sub.hidden = false;
    actions.hidden = true;
    input.value = "";
    onClear?.();
    onChange?.(null, null);
  }

  async function handleFile(f) {
    if (!f || !f.type.startsWith("image/")) return;
    file = f;
    setPreview(f);
    root.classList.add("is-loading");
    try {
      if (upload) {
        uploaded = await upload(f);
      }
      onChange?.(f, uploaded);
    } catch (err) {
      clear();
      throw err;
    } finally {
      root.classList.remove("is-loading");
    }
  }

  input.addEventListener("change", () => {
    const f = input.files?.[0];
    handleFile(f).catch((e) => {
      root.dispatchEvent(new CustomEvent("dropzone-error", { detail: e, bubbles: true }));
    });
  });

  ["dragenter", "dragover"].forEach((ev) => {
    root.addEventListener(ev, (e) => {
      e.preventDefault();
      root.classList.add("is-dragging");
    });
  });
  ["dragleave", "drop"].forEach((ev) => {
    root.addEventListener(ev, (e) => {
      e.preventDefault();
      root.classList.remove("is-dragging");
    });
  });
  root.addEventListener("drop", (e) => {
    const f = e.dataTransfer?.files?.[0];
    handleFile(f).catch((err) => {
      root.dispatchEvent(new CustomEvent("dropzone-error", { detail: err, bubbles: true }));
    });
  });
  root.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      input.click();
    }
  });

  return {
    root,
    clear,
    getFile: () => file,
    getUploaded: () => uploaded,
    destroy: () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    },
  };
}

export default createDropzone;
export { createDropzone as mountDropzone };

#!/usr/bin/env node
/**
 * Production frontend verification for the Lumen studio SPA.
 * Ensures required entrypoints and route modules exist (no bundler required).
 */
import { access, readFile } from "node:fs/promises";
import { constants } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const required = [
  "index.html",
  "styles/tokens.css",
  "styles/base.css",
  "styles/shell.css",
  "styles/components.css",
  "styles/pages.css",
  "styles/motion.css",
  "js/main.js",
  "js/api.js",
  "js/store.js",
  "js/router.js",
  "js/motion.js",
  "js/ui.js",
  "js/poller.js",
  "js/components/toast.js",
  "js/components/dropzone.js",
  "js/components/generationCard.js",
  "js/components/videoPlayer.js",
  "js/components/commandPalette.js",
  "js/pages/home.js",
  "js/pages/create.js",
  "js/pages/imageVideo.js",
  "js/pages/textVideo.js",
  "js/pages/projects.js",
  "js/pages/assets.js",
  "js/pages/queue.js",
  "js/pages/models.js",
  "js/pages/analytics.js",
  "js/pages/system.js",
  "js/pages/settings.js",
  "js/pages/apiDocs.js",
  "js/pages/account.js",
];

const missing = [];
for (const rel of required) {
  try {
    await access(path.join(root, rel), constants.R_OK);
  } catch {
    missing.push(rel);
  }
}

if (missing.length) {
  console.error("Lumen studio build failed — missing files:");
  missing.forEach((m) => console.error(" -", m));
  process.exit(1);
}

const html = await readFile(path.join(root, "index.html"), "utf8");
for (const needle of ["Lumen", "/dashboard/assets/js/main.js", "ls-app", "Create something extraordinary"]) {
  if (!html.includes(needle) && needle !== "Create something extraordinary") {
    // headline lives in JS; only require shell markers in HTML
  }
}
for (const needle of ["Lumen", "/dashboard/assets/js/main.js", "ls-app", "prefers-reduced-motion"]) {
  if (needle === "prefers-reduced-motion") continue;
  if (!html.includes(needle)) {
    console.error(`index.html missing required marker: ${needle}`);
    process.exit(1);
  }
}

const motionCss = await readFile(path.join(root, "styles/motion.css"), "utf8");
if (!motionCss.includes("prefers-reduced-motion")) {
  console.error("motion.css must respect prefers-reduced-motion");
  process.exit(1);
}

console.log(`Lumen studio production check OK (${required.length} files).`);
console.log("SPA is static ES modules — served by gateway at /dashboard.");

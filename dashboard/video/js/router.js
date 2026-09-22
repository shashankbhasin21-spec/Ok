const TITLES = {
  home: ["Home", "Create → Generate → Watch → Manage"],
  create: ["Create Studio", "Flagship cinematic workspace"],
  "image-video": ["Image → Video", "Animate a still with motion"],
  "text-video": ["Text → Video", "Prompt-first generation"],
  projects: ["Projects", "Your generation gallery"],
  assets: ["Assets", "Uploads, thumbnails, and READY videos"],
  queue: ["Generation Queue", "Live operational jobs"],
  models: ["Models", "Configured engines and capabilities"],
  analytics: ["Analytics", "Metrics from stored jobs only"],
  system: ["System", "Gateway, worker, and GPU diagnostics"],
  settings: ["Settings", "API key and studio preferences"],
  api: ["API", "Authenticated gateway reference"],
  account: ["Account", "Session and gateway identity"],
};

const VALID = new Set(Object.keys(TITLES));

export function parseRoute() {
  const hash = (location.hash || "#/home").replace(/^#\/?/, "");
  const [name, ...rest] = hash.split("/");
  const route = VALID.has(name) ? name : "home";
  return { route, parts: rest, title: TITLES[route] };
}

export function navigate(route, parts = []) {
  const clean = VALID.has(route) ? route : "home";
  const suffix = parts.filter(Boolean).length ? `/${parts.filter(Boolean).join("/")}` : "";
  const next = `#/${clean}${suffix}`;
  if (location.hash === next) {
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    return;
  }
  location.hash = next;
}

export function routeTitle(route) {
  return TITLES[route] || TITLES.home;
}

export { TITLES, VALID };

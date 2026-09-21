export const prefersReducedMotion = () =>
  typeof window !== "undefined" &&
  window.matchMedia &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function animateIn(el) {
  if (!el || prefersReducedMotion()) return;
  el.classList.add("ls-anim-in");
}

export function stagger(container) {
  if (!container || prefersReducedMotion()) return;
  container.classList.add("ls-stagger");
}

export function magneticButton(btn) {
  if (!btn || prefersReducedMotion()) return () => {};
  const strength = 8;
  const onMove = (e) => {
    const r = btn.getBoundingClientRect();
    const x = e.clientX - (r.left + r.width / 2);
    const y = e.clientY - (r.top + r.height / 2);
    btn.style.transform = `translate(${x / strength}px, ${y / strength}px)`;
  };
  const onLeave = () => {
    btn.style.transform = "";
  };
  btn.addEventListener("pointermove", onMove);
  btn.addEventListener("pointerleave", onLeave);
  return () => {
    btn.removeEventListener("pointermove", onMove);
    btn.removeEventListener("pointerleave", onLeave);
    btn.style.transform = "";
  };
}

export function tabIndicator(tabsEl) {
  if (!tabsEl) return () => {};
  let indicator = tabsEl.querySelector(".ls-tabs__indicator");
  if (!indicator) {
    indicator = document.createElement("span");
    indicator.className = "ls-tabs__indicator";
    indicator.setAttribute("aria-hidden", "true");
    tabsEl.prepend(indicator);
  }
  const update = () => {
    const active = tabsEl.querySelector('.ls-tabs__btn[aria-selected="true"]');
    if (!active) return;
    const parent = tabsEl.getBoundingClientRect();
    const rect = active.getBoundingClientRect();
    indicator.style.width = `${rect.width}px`;
    indicator.style.transform = `translateX(${rect.left - parent.left - 4}px)`;
  };
  update();
  const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(update) : null;
  ro?.observe(tabsEl);
  return { update, destroy: () => ro?.disconnect() };
}

export default {
  prefersReducedMotion,
  animateIn,
  stagger,
  magneticButton,
  tabIndicator,
};

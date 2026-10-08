// Use native, non-passive move/end listeners: React's delegated touch listeners
// cannot reliably prevent scrolling or the synthetic click after a long press.
export function attachChartTouchPreview(element: HTMLElement, preview: (index: number) => void) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let releaseTimer: ReturnType<typeof setTimeout> | undefined;
  let origin: { x: number; y: number } | null = null;
  let active = false;
  let suppressClick = false;

  const clearHold = () => { clearTimeout(timer); timer = undefined; };
  const start = (event: TouchEvent) => {
    clearHold(); clearTimeout(releaseTimer);
    active = false; origin = null; suppressClick = false;
    if (event.touches.length !== 1) return;
    const button = (event.target as Element).closest<HTMLElement>("[data-bar-index]");
    if (!button || !element.contains(button)) return;
    const touch = event.touches[0];
    origin = { x: touch.clientX, y: touch.clientY };
    timer = setTimeout(() => {
      active = true; suppressClick = true;
      preview(Number(button.dataset.barIndex));
    }, 450);
  };
  const finish = (event: TouchEvent) => {
    clearHold();
    if (active && event.cancelable && event.type === "touchend") event.preventDefault();
    active = false; origin = null;
    // Fallback for browsers that still synthesize a click after touchend.
    releaseTimer = setTimeout(() => { suppressClick = false; }, 1000);
  };
  const move = (event: TouchEvent) => {
    if (!origin) return;
    if (event.touches.length !== 1) { finish(event); return; }
    const touch = event.touches[0];
    if (!active) {
      if (Math.hypot(touch.clientX - origin.x, touch.clientY - origin.y) > 10) {
        clearHold(); origin = null; suppressClick = true;
      }
      return; // Ordinary swipes retain browser page/custom-chart scrolling.
    }
    if (event.cancelable) event.preventDefault();
    const bounds = element.getBoundingClientRect();
    if (touch.clientY < bounds.top || touch.clientY > bounds.bottom) return;
    const buttons = Array.from(element.querySelectorAll<HTMLElement>("[data-bar-index]"));
    let nearest: HTMLElement | undefined;
    let distance = Infinity;
    for (const button of buttons) {
      const rect = button.getBoundingClientRect();
      const delta = Math.abs(touch.clientX - (rect.left + rect.width / 2));
      if (delta < distance) { distance = delta; nearest = button; }
    }
    if (nearest) preview(Number(nearest.dataset.barIndex));
  };
  const click = (event: MouseEvent) => {
    if (suppressClick && event.detail !== 0) {
      event.preventDefault(); event.stopImmediatePropagation(); suppressClick = false;
    }
  };
  const context = (event: Event) => { if (origin || active) event.preventDefault(); };
  element.addEventListener("touchstart", start, { passive: true });
  element.addEventListener("touchmove", move, { passive: false });
  element.addEventListener("touchend", finish, { passive: false });
  element.addEventListener("touchcancel", finish);
  element.addEventListener("click", click, true);
  element.addEventListener("contextmenu", context);
  return () => {
    clearHold(); clearTimeout(releaseTimer);
    element.removeEventListener("touchstart", start);
    element.removeEventListener("touchmove", move);
    element.removeEventListener("touchend", finish);
    element.removeEventListener("touchcancel", finish);
    element.removeEventListener("click", click, true);
    element.removeEventListener("contextmenu", context);
  };
}

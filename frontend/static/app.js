// Shared delegated event handling.
//
// Replaces inline `onclick="fn(...)"` handlers (which the Content-Security-Policy
// `script-src-attr` now forbids) with data-attribute driven delegation:
//   <button data-click="clearCase">…</button>                    → window.clearCase()
//   <button data-click="buildIndex" data-click-arg="specs">…</button>
//   <div data-href="/cases/MS-001">…</div>                        → location.href = …
//   <input data-enter-action="doSearch">…</input>                 → Enter key calls window.doSearch()
//
// The target functions are resolved from window at event time, so the handler can be
// registered before the page's inline scripts have run.
(() => {
  const run = (el) => {
    if (el.dataset.href) {
      location.href = el.dataset.href;
      return true;
    }
    const action = el.dataset.click;
    if (!action || typeof window[action] !== "function") return false;
    window[action](el.dataset.clickArg);
    return true;
  };

  document.addEventListener("click", (event) => {
    const el = event.target.closest("[data-href], [data-click]");
    if (!el) return;
    event.preventDefault();
    run(el);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const el = event.target.closest("[data-enter-action]");
    if (!el || typeof window[el.dataset.enterAction] !== "function") return;
    event.preventDefault();
    window[el.dataset.enterAction]();
  });
})();

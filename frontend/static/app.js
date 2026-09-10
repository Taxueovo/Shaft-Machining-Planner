// 页面公共事件代理：将声明式按钮动作分派到已登记函数，减少内联脚本。
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
//
// 文件职责：全站统一的事件委托(代理)层。CSP 禁止内联 onclick 后，本脚本在 document
// 上监听 click 与 Enter 键，依据 data-href / data-click / data-enter-action 属性把事件
// 转发给 window 上对应的动作函数或完成页面跳转；目标函数在事件触发时才从 window 解析，
// 因此能在页面内联脚本执行前就注册好处理器。
(() => {
  // 执行单个委托元素 el：优先按 data-href 跳转；否则调用 window 上以 data-click 命名的
  // 动作函数(data-click-arg 作为参数)。命中返回 true(事件已消费)，未命中动作返回 false。
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

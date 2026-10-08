(() => {
  const U = window.ShaftUI;
  const feedback = document.getElementById("workspace-feedback");
  U.api("/api/jobs?limit=5").then(data => {
    const c = data.counts || {};
    document.getElementById("count-total").textContent = Object.values(c).reduce((a, b) => a + b, 0);
    document.getElementById("count-running").textContent = (c.running || 0) + (c.queued || 0) + (c.cancelling || 0);
    document.getElementById("count-waiting").textContent = (c.waiting_user_choice || 0) + (c.waiting_engineering_input || 0);
    document.getElementById("count-completed").textContent = c.completed || 0;
    document.getElementById("recent-jobs").innerHTML = U.taskTable(data.items, true);
  }).catch(error => {
    U.feedback(feedback, error.message);
    document.getElementById("recent-jobs").textContent = "任务暂时无法读取。可刷新页面重试。";
  });
  U.system().then(data => {
    document.getElementById("runtime-summary").innerHTML = `<div class="runtime-row"><span>规划模式</span><strong>${U.esc(U.modelLabel(data.model))}</strong></div><div class="runtime-row"><span>资源库文件</span><strong>${data.resources.machines && data.resources.tools ? "完整" : "待补充"}</strong></div><div class="runtime-row"><span>历史记忆</span><strong>${data.memory.enabled ? (data.memory.configured ? "已配置 · 待实连" : "配置未完整") : "未启用"}</strong></div><div class="runtime-row"><span>软件版本</span><strong>v${U.esc(data.product.version)}</strong></div>`;
  }).catch(() => {document.getElementById("runtime-summary").textContent = "暂时无法读取运行状态。";});
})();

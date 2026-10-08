/* Shared workspace presentation and bounded read-only requests. */
(() => {
  const esc = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  function errorText(body, fallback) {
    const detail = body?.detail?.detail || body?.detail || body;
    if (Array.isArray(detail)) return detail.map(x => `${(x.loc || []).filter(v => v !== "body").join(" / ")}：${x.msg}`).join("；");
    if (typeof detail === "string") return detail;
    return detail?.message || fallback;
  }
  async function api(path, options = {}) {
    const response = await fetch(path, {signal: AbortSignal.timeout(12000), ...options});
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new Error(errorText(body, `请求未成功（${response.status}），请稍后重试。`));
    if (!body) throw new Error("服务返回的数据无法读取，请刷新后重试。");
    return body;
  }
  const statuses = {
    queued: ["排队中", "neutral"], running: ["执行中", "neutral"],
    waiting_user_choice: ["待工艺选择", "warning"], waiting_engineering_input: ["待工程信息", "warning"],
    completed: ["已生成草案", "success"], failed: ["执行失败", "danger"],
    resource_mismatch: ["资源不匹配", "warning"], interrupted: ["重启中断", "warning"],
    cancelled: ["已取消", "neutral"], cancelling: ["正在取消", "warning"]
  };
  const badge = status => `<span class="badge ${(statuses[status] || ["", "neutral"])[1]}">${esc((statuses[status] || [status])[0])}</span>`;
  const date = value => {
    const stamp = new Date(value);
    return Number.isNaN(stamp.getTime()) ? "—" : new Intl.DateTimeFormat("zh-CN", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12:false}).format(stamp);
  };
  function taskTable(items, recent = false) {
    if (!items.length) return `<div class="task-empty"><strong>${recent ? "还没有规划任务" : "没有符合条件的任务"}</strong><p>${recent ? "按图纸录入零件，开始第一份工艺规划。" : "尝试更换筛选条件，或创建新任务。"}</p><a href="/custom" class="button primary">新建规划</a></div>`;
    return `<div class="table-wrap"><table class="task-table${recent ? " task-table-recent" : ""}"><thead><tr><th>零件 / 任务</th><th>状态</th>${recent ? "" : "<th>毛坯 / 总长</th>"}<th>创建时间</th><th>操作</th></tr></thead><tbody>${items.map(item => {
      const path = `/jobs/${encodeURIComponent(item.job_id)}`;
      const waiting = item.status.startsWith("waiting_");
      return `<tr><td><a class="task-name" href="${path}">${esc(item.title)}</a><span class="task-id">${esc(item.job_id)} · ${esc(item.material || "—")}</span></td><td>${badge(item.status)}</td>${recent ? "" : `<td>Ø${esc(item.blank_diameter_mm ?? "—")} × ${esc(item.total_length_mm ?? "—")} mm</td>`}<td>${esc(date(item.created_at))}</td><td><a class="text-link" href="${path}">${waiting ? "继续处理" : "查看"}</a>${recent ? "" : `<br><a class="text-link" href="/custom?from_job=${encodeURIComponent(item.job_id)}">从输入新建</a>`}</td></tr>`;
    }).join("")}</tbody></table></div>`;
  }
  let cachedSystem;
  function system(force = false) {
    if (!cachedSystem || force) cachedSystem = api("/api/system").catch(error => {cachedSystem = null; throw error;});
    return cachedSystem;
  }
  function feedback(element, message, danger = true) {
    element.innerHTML = message ? `<div class="alert ${danger ? "danger" : "warning"}">${esc(message)}</div>` : "";
  }
  const modelLabel = model => model.provider === "rules" ? "规则模式" : `${model.provider === "local" ? "本地模型" : "远程模型"}${model.available ? "已配置" : "未配置，使用规则回退"}`;
  window.ShaftUI = {esc, api, badge, date, taskTable, system, feedback, modelLabel, errorText};
  const indicator = document.getElementById("shell-service");
  system().then(data => {
    indicator.classList.add("online");
    indicator.querySelector("span").textContent = data.resources.machines && data.resources.tools ? "本机服务就绪" : "资源库待完善";
  }).catch(() => {
    indicator.classList.add("offline");
    indicator.querySelector("span").textContent = "后端暂不可用";
  });
})();

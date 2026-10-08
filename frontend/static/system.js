(() => {
  const U = window.ShaftUI, $ = id => document.getElementById(id);
  let current;
  const row = (label, value) => `<div class="data-row"><span>${U.esc(label)}</span><strong>${U.esc(value)}</strong></div>`;
  async function load(force = false) {
    $("system-refresh").disabled = true;
    U.feedback($("system-feedback"), "");
    try {
      current = await U.system(force);
      const d = current;
      $("system-overview").innerHTML = `<section class="panel"><h2>本机运行环境</h2>${row("软件版本", `v${d.product.version} · ${d.product.edition}`)}${row("任务记录", `${d.jobs.total_jobs} 条`)}${row("活动任务", `${d.jobs.active_jobs} 个`)}${row("关闭浏览器后", d.auto_shutdown_on_idle ? "空闲超时后退出（活动任务受保护）" : "服务继续运行")}<p class="muted">本机单进程工具。任务与检查点保存在本机，当前没有多用户权限系统。</p></section>
      <section class="panel"><h2>规划与资源</h2>${row("规划模式", U.modelLabel(d.model))}${row("模型名称", d.model.name || "无需模型")}${row("机床样例文件", d.resources.machines ? "存在" : "缺失")}${row("刀具样例文件", d.resources.tools ? "存在" : "缺失")}<p class="muted">文件存在仅说明样例库已安装，现场可用性仍需核实。模型配置不等于已验证调用成功。</p></section>
      <section class="panel"><h2>知识与历史经验</h2>${row("知识检索模块", d.rag.installed ? "已安装，索引状态见知识库" : "未安装（可选）")}${row("腾讯记忆", d.memory.enabled ? "已启用" : "未启用")}${row("记忆配置字段", d.memory.configured ? "已填写" : "未完整")}${row("记忆服务连接", "未主动探测")}<p class="muted">检查本页不会向外部模型或记忆服务发送零件信息。使用历史记忆前请完成独立部署与实连。</p><a href="/rag" class="text-link">打开知识库 →</a></section>
      <section class="panel"><h2>Engineering procedures and experience</h2>${Object.entries(d.engineering_skills?.versions || {}).map(([name,version]) => row(name, `v${version}`)).join("")}${row("Local lesson records", d.experience_library?.records ?? "Unavailable")}${row("Active reviewed lessons", d.experience_library?.active_reviewed ?? "Unavailable")}<p class="muted">Manage lessons on the source task result page. New tasks retrieve applicable, reviewed, unexpired advice. Existing tasks retain their original snapshots.</p></section>
      <section class="panel"><h2>工程使用边界</h2><p>系统输出为工艺草案。规则校验、专家建议与资源样例匹配用于辅助复核，不构成生产放行。</p><p class="muted">请依据当前图纸、实际设备与刀具、装夹方案、检验规范及外协能力确认结果。</p><span class="badge warning">工程复核必需</span></section>`;
    } catch (error) {U.feedback($("system-feedback"), error.message);} finally {$("system-refresh").disabled = false;}
  }
  $("system-refresh").addEventListener("click", () => load(true));
  $("system-stop").addEventListener("click", async () => {
    if (!confirm(`确认停止 shaftmachiningplanner 本机服务？${current?.jobs.active_jobs ? `当前有 ${current.jobs.active_jobs} 个活动任务，执行中的任务将中断。` : "请先保存表单草稿。"}`)) return;
    $("system-stop").disabled = true;
    try {
      await U.api("/api/shutdown", {method:"POST"});
      $("main-content").innerHTML = '<section class="panel"><h1>已发送停止请求</h1><p class="muted">可以关闭此页面。下次使用时重新运行启动器。</p></section>';
    } catch (error) {U.feedback($("system-feedback"), error.message); $("system-stop").disabled = false;}
  });
  load();
})();

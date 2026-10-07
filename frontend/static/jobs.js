(() => {
  const U = window.ShaftUI, $ = id => document.getElementById(id);
  let offset = 0, requestNumber = 0;
  const limit = 20;
  const initial = new URLSearchParams(location.search);
  if ([...$("job-filter").options].some(o => o.value === initial.get("status"))) $("job-filter").value = initial.get("status");
  $("job-search").value = initial.get("search") || "";
  async function load() {
    const number = ++requestNumber;
    const params = new URLSearchParams({limit, offset});
    if ($("job-filter").value) params.set("status", $("job-filter").value);
    if ($("job-search").value.trim()) params.set("search", $("job-search").value.trim());
    $("jobs-refresh").disabled = true; $("jobs-prev").disabled = true; $("jobs-next").disabled = true;
    $("jobs-list").setAttribute("aria-busy", "true");
    U.feedback($("jobs-feedback"), "");
    try {
      const data = await U.api(`/api/jobs?${params}`);
      if (number !== requestNumber) return;
      if (offset >= data.total && offset > 0) {offset = Math.max(0, Math.floor((data.total - 1) / limit) * limit); return load();}
      $("jobs-list").innerHTML = U.taskTable(data.items);
      $("jobs-count").textContent = data.total ? `共 ${data.total} 条 · 第 ${Math.floor(offset / limit) + 1} / ${Math.ceil(data.total / limit)} 页` : "共 0 条记录";
      $("jobs-prev").disabled = offset === 0;
      $("jobs-next").disabled = offset + limit >= data.total;
      const address = new URLSearchParams(params); address.delete("limit"); address.delete("offset");
      history.replaceState(null, "", `/jobs${address.size ? "?" + address : ""}`);
    } catch (error) {
      if (number !== requestNumber) return;
      U.feedback($("jobs-feedback"), error.message);
      $("jobs-list").textContent = "列表暂不可用，请点击刷新重试。";
      $("jobs-count").textContent = "";
    } finally {
      if (number === requestNumber) {$("jobs-refresh").disabled = false; $("jobs-list").setAttribute("aria-busy", "false");}
    }
  }
  $("jobs-filter").addEventListener("submit", event => {event.preventDefault(); offset = 0; load();});
  $("job-filter").addEventListener("change", () => {offset = 0; load();});
  $("jobs-refresh").addEventListener("click", load);
  $("jobs-prev").addEventListener("click", () => {offset = Math.max(0, offset - limit); load();});
  $("jobs-next").addEventListener("click", () => {offset += limit; load();});
  load();
})();

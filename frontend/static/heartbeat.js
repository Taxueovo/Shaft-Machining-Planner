/**
 * Shaft Machining Planner Heartbeat — keeps backend watchdog alive.
 * Include this script in every page.
 * If no heartbeat is received by the backend within HEARTBEAT_TIMEOUT (default 30s),
 * both frontend and backend auto-shutdown.
 */
// 文件职责：后台心跳脚本(需随每个页面引入)。周期性向 /api/heartbeat 发送 POST，
// 让后端监视器持续收到心跳，避免因超时(默认 30s)触发前端与后端自动关停。
// 页面加载后立即心跳一次；页面卸载时以 sendBeacon 补发最后一跳，保证进程能及时感知退出。
(() => {
  const INTERVAL = 10_000; // 10 seconds

  // 发送单次心跳：keepalive 让请求在页面即将卸载时也尽量送达；catch(() => {})
  // 静默忽略失败，网络瞬时不可用不应打断当前页面。
  function beat() {
    fetch("/api/heartbeat", { method: "POST", keepalive: true }).catch(() => {});
  }

  // Periodic heartbeat
  setInterval(beat, INTERVAL);

  // Immediate heartbeat on page unload (uses keepalive for reliability)
  window.addEventListener("beforeunload", () => {
    navigator.sendBeacon("/api/heartbeat");
  });

  // First heartbeat on page load
  beat();
})();

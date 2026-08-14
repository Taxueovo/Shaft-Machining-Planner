/**
 * Shaft Machining Planner Heartbeat — keeps backend watchdog alive.
 * Include this script in every page.
 * If no heartbeat is received by the backend within HEARTBEAT_TIMEOUT (default 30s),
 * both frontend and backend auto-shutdown.
 */
(() => {
  const INTERVAL = 10_000; // 10 seconds

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
